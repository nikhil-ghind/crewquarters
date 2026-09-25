"""Model gateway tests: leases, admission, load/unload, reaping, crash handling,
run authorization, budgets, and the cloud adapters (mocked HTTP)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anthropic
import httpx
import httpx2
import pytest
from sqlalchemy import select, text

from crewquarters_gateway.adapters import AnthropicAdapter, ChatRequest, OpenAIAdapter
from crewquarters_gateway.config import GatewaySettings, GiB
from crewquarters_gateway.credentials import StaticCredentials
from crewquarters_gateway.main import Gateway, create_app
from crewquarters_gateway.runtime import InProcessModelRuntime
from crewquarters_shared import capability
from crewquarters_shared.db.models import AuditEvent
from crewquarters_shared.db.models_gateway import LlmUsage, ModelInstance, ModelLease
from crewquarters_shared.errors import PlatformError

ROOT = Path(__file__).resolve().parents[3]
TOKEN = "dev-insecure-internal-token-change-me-0000"
SIGNING = "dev-insecure-capability-key-change-me-00000"
CHAT_TOKEN = "dev-insecure-chat-token-change-me-000000000"
SERVICE = {"Authorization": f"Bearer {TOKEN}", "X-Chat-Client-Token": CHAT_TOKEN}
SMALL, QUALITY = "local.general.small", "local.general.quality"


class FakeControl:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.signals: list[tuple[str, bool]] = []

    async def get_run(self, run_id: str, max_age: float = 0.0) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    async def run_is_active(self, run_id: str) -> bool:
        run = self.runs.get(run_id)
        return bool(run) and run["state"] in {
            "RUNNING",
            "LOADING_MODEL",
            "WAITING_INPUT",
            "PREPARING",
        }

    async def set_model_loading(
        self, run_id: str, attempt: int, loading: bool, model: str | None
    ) -> None:
        self.signals.append((run_id, loading))

    async def close(self) -> None:
        return None


def gateway_settings(**overrides: Any) -> GatewaySettings:
    base = {
        "catalog_dir": ROOT / "catalog/models/dev",
        "runtime": "inprocess",
        "system_reserve_bytes": 2 * GiB,
        "max_serving_bytes": 16 * GiB,
        "load_safety_margin_bytes": 1 * GiB,
        "idle_unload_seconds": 3600,
        "load_poll_seconds": 0.05,
        "wait_ready_seconds": 10,
        "manual_drain_seconds": 1,
    }
    base.update(overrides)
    return GatewaySettings(**base)


@pytest.fixture
async def gateway(settings) -> AsyncIterator[Gateway]:  # type: ignore[no-untyped-def]
    gw = Gateway(
        settings, gateway_settings(), runtime=InProcessModelRuntime(), control=FakeControl()
    )  # type: ignore[arg-type]
    await gw.sync_catalog()
    stop = asyncio.Event()
    worker = asyncio.create_task(gw.worker_loop(stop))
    try:
        yield gw
    finally:
        stop.set()
        await worker
        await gw.close()


@pytest.fixture
async def client(gateway: Gateway) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(gateway, background=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway", headers=SERVICE
    ) as c:
        yield c


async def install(client: httpx.AsyncClient, model: str = SMALL) -> None:
    response = await client.post(f"/internal/v1/models/{model}/install")
    assert response.status_code == 202, response.text
    assert response.json()["downloadState"] == "INSTALLED"


def chat_body(**extra: Any) -> dict[str, Any]:
    return {
        "profile": SMALL,
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "hello crew"},
        ],
        "maxOutputTokens": 50,
        "holder": {"type": "chat", "id": "session-1", "label": "My chat"},
        **extra,
    }


def run_token(
    gateway: Gateway, run_id: str, attempt: int = 1, caps: list[str] | None = None
) -> tuple[str, str]:
    token, claims = capability.mint(
        signing_key=SIGNING,
        run_id=uuid.UUID(run_id),
        attempt=attempt,
        installation_id=uuid.uuid4(),
        agent_version_id=uuid.uuid4(),
        capabilities=caps if caps is not None else [f"llm.profile:{SMALL}"],
        resources={"modelBindings": {"local.general": SMALL}},
        ttl_seconds=600,
    )
    control: FakeControl = gateway.control  # type: ignore[assignment]
    control.runs[run_id] = {
        "state": "RUNNING",
        "currentAttempt": attempt,
        "capabilityTokenId": claims.token_id,
    }
    return token, claims.token_id


# --- catalog and auth ---------------------------------------------------------------------


async def test_requires_service_token(client: httpx.AsyncClient) -> None:
    response = await client.get("/internal/v1/models", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


async def test_catalog_lists_pinned_profiles(client: httpx.AsyncClient) -> None:
    models = (await client.get("/internal/v1/models")).json()
    assert [m["id"] for m in models] == [QUALITY, SMALL]
    small = next(m for m in models if m["id"] == SMALL)
    assert small["downloadState"] == "NOT_INSTALLED" and small["memoryState"] == "NOT_LOADED"
    assert small["expectedMemoryBytes"] == 2 * GiB and small["validation"] == "mock"


# --- lease -> load -> serve -> idle unload ----------------------------------------------------


async def test_chat_loads_on_demand_and_idle_unloads(
    client: httpx.AsyncClient, gateway: Gateway
) -> None:
    not_installed = await client.post("/internal/v1/llm/chat", json=chat_body())
    assert (
        not_installed.status_code == 409
        and not_installed.json()["error"]["code"] == "MODEL_NOT_INSTALLED"
    )
    await install(client)

    response = await client.post("/internal/v1/llm/chat", json=chat_body())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == "Mock reply to: hello crew"
    assert body["provider"] == "local" and body["usage"]["outputTokens"] == 5
    model = (await client.get(f"/internal/v1/models/{SMALL}")).json()
    assert model["memoryState"] == "READY" and model["reservedBytes"] == 2 * GiB
    assert [lease["holderType"] for lease in model["activeLeases"]] == ["chat"]

    released = await client.delete("/internal/v1/leases/holders/chat/session-1")
    assert released.json() == {"released": 1}
    gateway.settings.idle_unload_seconds = 0
    await gateway.manager.reap()  # sets idle deadline / drains
    await gateway.manager.reap()
    for _ in range(100):
        if (await client.get(f"/internal/v1/models/{SMALL}")).json()["memoryState"] == "NOT_LOADED":
            break
        await asyncio.sleep(0.05)
    model = (await client.get(f"/internal/v1/models/{SMALL}")).json()
    assert model["memoryState"] == "NOT_LOADED" and model["reservedBytes"] == 0
    runtime: InProcessModelRuntime = gateway.runtime  # type: ignore[assignment]
    assert runtime.starts == [SMALL] and runtime.stops == [SMALL]
    async with gateway.sessions() as db:
        rows = (await db.scalars(select(LlmUsage))).all()
    assert len(rows) == 1 and rows[0].holder_type == "chat" and rows[0].outcome == "ok"


async def test_concurrent_cold_requests_start_one_server(
    client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(client)
    bodies = [chat_body(holder={"type": "chat", "id": f"s{i}", "label": "c"}) for i in range(6)]
    responses = await asyncio.gather(
        *(client.post("/internal/v1/llm/chat", json=b) for b in bodies)
    )
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    runtime: InProcessModelRuntime = gateway.runtime  # type: ignore[assignment]
    assert runtime.starts == [SMALL]
    model = (await client.get(f"/internal/v1/models/{SMALL}")).json()
    assert len(model["activeLeases"]) == 6


async def test_streaming_chat(client: httpx.AsyncClient) -> None:
    await install(client)
    async with client.stream(
        "POST", "/internal/v1/llm/chat", json=chat_body(stream=True)
    ) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [
            json.loads(line[6:])
            async for line in response.aiter_lines()
            if line.startswith("data: ")
        ]
    text_out = "".join(e["text"] for e in events if e["type"] == "delta")
    assert text_out == "Mock reply to: hello crew"
    assert events[-1]["type"] == "done" and events[-1]["response"]["text"] == text_out


async def test_structured_output(client: httpx.AsyncClient) -> None:
    await install(client)
    schema = {
        "type": "object",
        "required": ["urgent", "count"],
        "properties": {"urgent": {"type": "array"}, "count": {"type": "integer"}},
    }
    body = (
        await client.post("/internal/v1/llm/chat", json=chat_body(responseSchema=schema))
    ).json()
    assert body["structured"] == {"urgent": [], "count": 0}


async def test_tools_fail_clearly(client: httpx.AsyncClient) -> None:
    await install(client)
    response = await client.post("/internal/v1/llm/chat", json=chat_body(tools=[{"name": "x"}]))
    assert response.status_code == 422 and response.json()["error"]["code"] == "UNSUPPORTED_FEATURE"


# --- admission control --------------------------------------------------------------------------


async def test_one_generative_model_policy(client: httpx.AsyncClient) -> None:
    await install(client)
    await install(client, QUALITY)
    assert (await client.post(f"/internal/v1/models/{SMALL}/load")).status_code == 202
    blocked = await client.post(f"/internal/v1/models/{QUALITY}/load")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "MODEL_CAPACITY_EXCEEDED"
    assert blocked.json()["error"]["details"]["loadedModels"] == [SMALL]


async def test_serving_limit_and_host_memory(client: httpx.AsyncClient, gateway: Gateway) -> None:
    await install(client, QUALITY)
    gateway.settings.max_serving_bytes = 4 * GiB
    over = await client.post(f"/internal/v1/models/{QUALITY}/load")
    assert over.status_code == 409 and "maxServingBytes" in over.json()["error"]["details"]
    gateway.settings.max_serving_bytes = 96 * GiB
    runtime: InProcessModelRuntime = gateway.runtime  # type: ignore[assignment]
    runtime.available_bytes = 5 * GiB  # 5 - 2 reserve < 6 + 1
    low = await client.post(f"/internal/v1/models/{QUALITY}/load")
    assert low.status_code == 409 and "availableBytes" in low.json()["error"]["details"]
    async with gateway.sessions() as db:
        instance = await db.get(ModelInstance, QUALITY)
        assert instance.state == "NOT_LOADED" and instance.reserved_bytes == 0


# --- failures -------------------------------------------------------------------------------------


async def test_load_failure_then_retry(client: httpx.AsyncClient, gateway: Gateway) -> None:
    await install(client)
    runtime: InProcessModelRuntime = gateway.runtime  # type: ignore[assignment]
    runtime.fail_start.add(SMALL)
    failed = await client.post("/internal/v1/llm/chat", json=chat_body())
    assert failed.status_code == 503 and failed.json()["error"]["code"] == "MODEL_LOAD_FAILED"
    model = (await client.get(f"/internal/v1/models/{SMALL}")).json()
    assert model["memoryState"] == "LOAD_ERROR" and model["reservedBytes"] == 0
    runtime.fail_start.clear()
    assert (await client.post("/internal/v1/llm/chat", json=chat_body())).status_code == 200


async def test_crash_is_detected_and_reloaded(client: httpx.AsyncClient, gateway: Gateway) -> None:
    await install(client)
    assert (await client.post("/internal/v1/llm/chat", json=chat_body())).status_code == 200
    runtime: InProcessModelRuntime = gateway.runtime  # type: ignore[assignment]
    runtime.crash.add(SMALL)
    report = await gateway.manager.reap()
    assert report["crashed"] == 1
    model = (await client.get(f"/internal/v1/models/{SMALL}")).json()
    assert model["memoryState"] == "ERROR" and model["error"]["oomKilled"] is True
    again = await client.post("/internal/v1/llm/chat", json=chat_body())
    assert again.status_code == 200
    assert runtime.starts == [SMALL, SMALL]


async def test_manual_unload_respects_leases(client: httpx.AsyncClient, gateway: Gateway) -> None:
    await install(client)
    await client.post("/internal/v1/llm/chat", json=chat_body())
    in_use = await client.post(f"/internal/v1/models/{SMALL}/unload", json={})
    assert in_use.status_code == 409 and in_use.json()["error"]["details"]["holders"] == [
        "Chat: My chat"
    ]
    forced = await client.post(f"/internal/v1/models/{SMALL}/unload", json={"force": True})
    assert forced.json()["memoryState"] == "DRAINING"
    blocked = await client.post("/internal/v1/llm/chat", json=chat_body())
    assert blocked.status_code in (409, 503)
    for _ in range(100):
        if (await client.get(f"/internal/v1/models/{SMALL}")).json()["memoryState"] == "NOT_LOADED":
            break
        await asyncio.sleep(0.05)
    assert (await client.get(f"/internal/v1/models/{SMALL}")).json()["memoryState"] == "NOT_LOADED"
    busy = await client.delete(f"/internal/v1/models/{QUALITY}/files")
    assert busy.status_code == 200


async def test_new_lease_cancels_idle_drain(client: httpx.AsyncClient, gateway: Gateway) -> None:
    await install(client)
    await client.post("/internal/v1/llm/chat", json=chat_body())
    await client.delete("/internal/v1/leases/holders/chat/session-1")
    async with gateway.sessions() as db, db.begin():
        instance = await db.get(ModelInstance, SMALL, with_for_update=True)
        instance.state, instance.drain_reason = "DRAINING", "idle"
    await gateway.manager.acquire(SMALL, "chat", "s2", "c", 60)
    async with gateway.sessions() as db:
        assert (await db.get(ModelInstance, SMALL)).state == "READY"


async def test_expired_leases_are_released(client: httpx.AsyncClient, gateway: Gateway) -> None:
    await install(client)
    await client.post("/internal/v1/llm/chat", json=chat_body())
    async with gateway.sessions() as db, db.begin():
        await db.execute(text("UPDATE model_leases SET expires_at = now() - interval '1 second'"))
    assert (await gateway.manager.reap())["expired"] == 1
    async with gateway.sessions() as db:
        lease = await db.scalar(select(ModelLease))
        assert lease.release_reason == "expired"
        assert (await db.get(ModelInstance, SMALL)).idle_since is not None


# --- runs -----------------------------------------------------------------------------------------


async def test_run_calls_require_active_attempt_and_capability(
    client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(client)
    run_id = str(uuid.uuid4())
    token, _ = run_token(gateway, run_id)
    body = {"profile": "local.general", "messages": [{"role": "user", "content": "hi"}]}
    ok = await client.post(
        "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": token}
    )
    assert ok.status_code == 200, ok.text
    control: FakeControl = gateway.control  # type: ignore[assignment]
    assert control.signals == [(run_id, True), (run_id, False)]  # LOADING_MODEL while cold

    denied = await client.post(
        "/internal/v1/llm/chat",
        json={**body, "profile": QUALITY},
        headers={"X-Capability-Token": token},
    )
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "PERMISSION_DENIED"

    control.runs[run_id]["currentAttempt"] = 2  # a retry made this token stale
    stale = await client.post(
        "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": token}
    )
    assert stale.status_code == 403 and stale.json()["error"]["code"] == "RUN_NOT_ACTIVE"

    forged = await client.post(
        "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": token + "x"}
    )
    assert forged.status_code == 401

    chat_cannot_impersonate = await client.post("/internal/v1/llm/chat", json=body)
    assert chat_cannot_impersonate.status_code == 401


async def test_run_leases_release_when_run_finishes(
    client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(client)
    run_id = str(uuid.uuid4())
    token, _ = run_token(gateway, run_id)
    await client.post(
        "/internal/v1/llm/chat",
        json={"profile": "local.general", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-Capability-Token": token},
    )
    control: FakeControl = gateway.control  # type: ignore[assignment]
    control.runs[run_id]["state"] = "SUCCEEDED"
    assert (await gateway.manager.reap(control.run_is_active))["runReleased"] == 1


async def test_per_run_token_budget(client: httpx.AsyncClient, gateway: Gateway) -> None:
    await install(client)
    # Each request reserves prompt/4 + maxOutputTokens (3 + 10) before it runs.
    gateway.settings.per_run_token_limit = 20
    run_id = str(uuid.uuid4())
    token, _ = run_token(gateway, run_id)
    body = {
        "profile": SMALL,
        "messages": [{"role": "user", "content": "one two three"}],
        "maxOutputTokens": 10,
    }
    first = await client.post(
        "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": token}
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": token}
    )
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "RUN_TOKEN_BUDGET_EXCEEDED"


# --- cloud ----------------------------------------------------------------------------------------


async def test_cloud_requires_explicit_permission_and_credentials(
    client: httpx.AsyncClient, gateway: Gateway
) -> None:
    run_id = str(uuid.uuid4())
    local_only, _ = run_token(gateway, run_id)
    body = {"profile": "anthropic.default", "messages": [{"role": "user", "content": "hi"}]}
    denied = await client.post(
        "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": local_only}
    )
    assert denied.status_code == 403
    cloud_run = str(uuid.uuid4())
    cloud, _ = run_token(
        gateway, cloud_run, caps=["cloud.anthropic", "llm.profile:anthropic.default"]
    )
    missing = await client.post(
        "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": cloud}
    )
    assert (
        missing.status_code == 409
        and missing.json()["error"]["code"] == "CLOUD_PROVIDER_NOT_CONFIGURED"
    )
    openai_body = {**body, "profile": "openai.default"}
    openai_run = str(uuid.uuid4())
    oai, _ = run_token(gateway, openai_run, caps=["cloud.openai", "llm.profile:openai.default"])
    unconfigured = await client.post(
        "/internal/v1/llm/chat", json=openai_body, headers={"X-Capability-Token": oai}
    )
    assert unconfigured.json()["error"]["code"] == "CLOUD_PROFILE_NOT_CONFIGURED"
    chat_cloud = await client.post(
        "/internal/v1/llm/chat", json={**body, "holder": {"type": "chat", "id": "c"}}
    )
    assert chat_cloud.status_code == 403  # chat is local-only


def anthropic_client(handler: Any) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key="sk-test",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )


async def test_anthropic_adapter_request_and_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta")
        seen["key"] = request.headers.get("x-api-key")
        return httpx2.Response(
            200,
            headers={"request-id": "req_123"},
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [{"type": "text", "text": '{"answer": "yes"}'}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 4},
            },
        )

    adapter = AnthropicAdapter("sk-test", "claude-opus-5", 30, client=anthropic_client(handler))
    schema = {
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
        "additionalProperties": False,
    }
    result = await adapter.chat(
        ChatRequest(
            messages=[
                {"role": "system", "content": "Be terse."},
                {"role": "user", "content": "ok?"},
            ],
            max_output_tokens=100,
            temperature=0.2,
            response_schema=schema,
        )
    )
    body = seen["body"]
    assert body["model"] == "claude-opus-5" and body["system"] == "Be terse."
    assert body["messages"] == [{"role": "user", "content": "ok?"}]
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": schema}}
    assert body["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in seen["beta"]
    assert "temperature" not in body and seen["key"] == "sk-test"
    assert result.structured == {"answer": "yes"} and result.request_id == "req_123"
    assert result.input_tokens == 12 and result.ignored_parameters == ["temperature"]


async def test_anthropic_refusal_is_reported() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "id": "msg_2",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [],
                "stop_reason": "refusal",
                "stop_sequence": None,
                "stop_details": {"type": "refusal", "category": "cyber", "explanation": None},
                "usage": {"input_tokens": 3, "output_tokens": 0},
            },
        )

    adapter = AnthropicAdapter("sk-test", "claude-opus-5", 30, client=anthropic_client(handler))
    result = await adapter.chat(
        ChatRequest(messages=[{"role": "user", "content": "x"}], max_output_tokens=10)
    )
    # The adapter reports the refusal with its usage; the inference layer records the
    # usage and then raises MODEL_REFUSED (see test_gateway_regressions.py).
    assert result.finish_reason == "refusal" and result.refusal_category == "cyber"
    assert result.input_tokens == 3


async def test_anthropic_errors_are_classified() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}
        )

    adapter = AnthropicAdapter("sk-test", "claude-opus-5", 30, client=anthropic_client(handler))
    with pytest.raises(PlatformError) as err:
        await adapter.chat(
            ChatRequest(messages=[{"role": "user", "content": "x"}], max_output_tokens=10)
        )
    assert err.value.code == "PROVIDER_RATE_LIMITED"


async def test_anthropic_streaming() -> None:
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "m",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-opus-5",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 5, "output_tokens": 1},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hel"},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "lo"},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 2},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    payload = "".join(
        f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events
    ).encode()

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=payload)

    adapter = AnthropicAdapter("sk-test", "claude-opus-5", 30, client=anthropic_client(handler))
    out = [
        e
        async for e in adapter.stream(
            ChatRequest(messages=[{"role": "user", "content": "x"}], max_output_tokens=10)
        )
    ]
    assert [e["text"] for e in out if e["type"] == "delta"] == ["Hel", "lo"]
    final = out[-1]["result"]
    assert final.text == "Hello" and final.output_tokens == 2 and final.finish_reason == "stop"


async def test_openai_adapter_responses_api() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"x-request-id": "req_oai"},
            json={
                "id": "resp_1",
                "model": "test-model",
                "status": "completed",
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": '{"n": 1}'}]}
                ],
                "usage": {"input_tokens": 7, "output_tokens": 3},
            },
        )

    adapter = OpenAIAdapter("sk-oai", "test-model", 30, transport=httpx.MockTransport(handler))
    schema = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}
    result = await adapter.chat(
        ChatRequest(
            messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
            max_output_tokens=20,
            temperature=0.1,
            response_schema=schema,
        )
    )
    assert seen["path"] == "/v1/responses" and seen["auth"] == "Bearer sk-oai"
    body = seen["body"]
    assert body["instructions"] == "sys" and body["input"] == [{"role": "user", "content": "go"}]
    assert body["text"]["format"]["type"] == "json_schema" and body["store"] is False
    assert (
        result.structured == {"n": 1}
        and result.request_id == "req_oai"
        and result.output_tokens == 3
    )


async def test_cloud_call_is_audited_and_counted(
    client: httpx.AsyncClient, gateway: Gateway
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "id": "m",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [{"type": "text", "text": "hi"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        )

    gateway.inference.credentials = StaticCredentials({"anthropic": "sk-test"})
    original = gateway.inference._cloud_adapter

    async def patched(provider: str, model: str) -> Any:
        adapter = await original(provider, model)
        adapter.client = anthropic_client(handler)  # type: ignore[attr-defined]
        return adapter

    gateway.inference._cloud_adapter = patched  # type: ignore[method-assign]
    run_id = str(uuid.uuid4())
    token, _ = run_token(gateway, run_id, caps=["cloud.anthropic", "llm.profile:anthropic.default"])
    response = await client.post(
        "/internal/v1/llm/chat",
        json={
            "profile": "anthropic.default",
            "messages": [{"role": "user", "content": "secret prompt"}],
        },
        headers={"X-Capability-Token": token},
    )
    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "anthropic"
    async with gateway.sessions() as db:
        event = await db.scalar(select(AuditEvent).where(AuditEvent.action == "llm.cloud_call"))
        usage = await db.scalar(select(LlmUsage))
    assert event is not None and "secret prompt" not in json.dumps(event.metadata_)
    assert usage.provider == "anthropic" and usage.input_tokens == 2
