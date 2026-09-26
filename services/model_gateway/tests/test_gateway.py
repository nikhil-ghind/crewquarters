"""Model gateway tests: leases, admission, load/unload, reaping, crash handling,
run authorization, budgets, and the cloud adapters (mocked HTTP)."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
import httpx2
import pytest
from gateway_helpers import (
    QUALITY,
    SMALL,
    VISION,
    FakeControl,
    anthropic_client,
    chat_body,
    install,
    mock_cloud,
    run_token,
)
from sqlalchemy import select, text

from crewquarters_gateway.adapters import AnthropicAdapter, ChatRequest, OpenAIAdapter
from crewquarters_gateway.config import GiB
from crewquarters_gateway.credentials import StaticCredentials
from crewquarters_gateway.main import Gateway
from crewquarters_gateway.runtime import InProcessModelRuntime
from crewquarters_shared.db.models import AuditEvent
from crewquarters_shared.db.models_gateway import LlmUsage, ModelInstance, ModelLease
from crewquarters_shared.errors import PlatformError

# --- catalog and auth ---------------------------------------------------------------------


async def test_requires_service_token(gw_client: httpx.AsyncClient) -> None:
    response = await gw_client.get("/internal/v1/models", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


async def test_catalog_lists_pinned_profiles(gw_client: httpx.AsyncClient) -> None:
    models = (await gw_client.get("/internal/v1/models")).json()
    assert [m["id"] for m in models] == [QUALITY, SMALL, VISION]
    small = next(m for m in models if m["id"] == SMALL)
    assert small["downloadState"] == "NOT_INSTALLED" and small["memoryState"] == "NOT_LOADED"
    assert small["expectedMemoryBytes"] == 2 * GiB and small["validation"] == "mock"


# --- lease -> load -> serve -> idle unload ----------------------------------------------------


async def test_chat_loads_on_demand_and_idle_unloads(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    not_installed = await gw_client.post("/internal/v1/llm/chat", json=chat_body())
    assert (
        not_installed.status_code == 409
        and not_installed.json()["error"]["code"] == "MODEL_NOT_INSTALLED"
    )
    await install(gw_client)

    response = await gw_client.post("/internal/v1/llm/chat", json=chat_body())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == "Mock reply to: hello crew"
    assert body["provider"] == "local" and body["usage"]["outputTokens"] == 5
    model = (await gw_client.get(f"/internal/v1/models/{SMALL}")).json()
    assert model["memoryState"] == "READY" and model["reservedBytes"] == 2 * GiB
    assert [lease["holderType"] for lease in model["activeLeases"]] == ["chat"]

    released = await gw_client.delete("/internal/v1/leases/holders/chat/session-1")
    assert released.json() == {"released": 1}
    gateway.settings.idle_unload_seconds = 0
    await gateway.manager.reap()  # sets idle deadline / drains
    await gateway.manager.reap()
    for _ in range(100):
        if (await gw_client.get(f"/internal/v1/models/{SMALL}")).json()[
            "memoryState"
        ] == "NOT_LOADED":
            break
        await asyncio.sleep(0.05)
    model = (await gw_client.get(f"/internal/v1/models/{SMALL}")).json()
    assert model["memoryState"] == "NOT_LOADED" and model["reservedBytes"] == 0
    runtime: InProcessModelRuntime = gateway.runtime  # type: ignore[assignment]
    assert runtime.starts == [SMALL] and runtime.stops == [SMALL]
    async with gateway.sessions() as db:
        rows = (await db.scalars(select(LlmUsage))).all()
    assert len(rows) == 1 and rows[0].holder_type == "chat" and rows[0].outcome == "ok"


async def test_concurrent_cold_requests_start_one_server(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client)
    bodies = [chat_body(holder={"type": "chat", "id": f"s{i}", "label": "c"}) for i in range(6)]
    responses = await asyncio.gather(
        *(gw_client.post("/internal/v1/llm/chat", json=b) for b in bodies)
    )
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    runtime: InProcessModelRuntime = gateway.runtime  # type: ignore[assignment]
    assert runtime.starts == [SMALL]
    model = (await gw_client.get(f"/internal/v1/models/{SMALL}")).json()
    assert len(model["activeLeases"]) == 6


async def test_streaming_chat(gw_client: httpx.AsyncClient) -> None:
    await install(gw_client)
    async with gw_client.stream(
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


async def test_structured_output(gw_client: httpx.AsyncClient) -> None:
    await install(gw_client)
    schema = {
        "type": "object",
        "required": ["urgent", "count"],
        "properties": {"urgent": {"type": "array"}, "count": {"type": "integer"}},
    }
    body = (
        await gw_client.post("/internal/v1/llm/chat", json=chat_body(responseSchema=schema))
    ).json()
    assert body["structured"] == {"urgent": [], "count": 0}


async def test_tools_fail_clearly(gw_client: httpx.AsyncClient) -> None:
    await install(gw_client)
    response = await gw_client.post("/internal/v1/llm/chat", json=chat_body(tools=[{"name": "x"}]))
    assert response.status_code == 422 and response.json()["error"]["code"] == "UNSUPPORTED_FEATURE"


# --- admission control --------------------------------------------------------------------------


async def test_one_generative_model_policy(gw_client: httpx.AsyncClient) -> None:
    await install(gw_client)
    await install(gw_client, QUALITY)
    assert (await gw_client.post(f"/internal/v1/models/{SMALL}/load")).status_code == 202
    blocked = await gw_client.post(f"/internal/v1/models/{QUALITY}/load")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "MODEL_CAPACITY_EXCEEDED"
    assert blocked.json()["error"]["details"]["loadedModels"] == [SMALL]


async def test_serving_limit_and_host_memory(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client, QUALITY)
    gateway.settings.max_serving_bytes = 4 * GiB
    over = await gw_client.post(f"/internal/v1/models/{QUALITY}/load")
    assert over.status_code == 409 and "maxServingBytes" in over.json()["error"]["details"]
    gateway.settings.max_serving_bytes = 96 * GiB
    runtime: InProcessModelRuntime = gateway.runtime  # type: ignore[assignment]
    runtime.available_bytes = 5 * GiB  # 5 - 2 reserve < 6 + 1
    low = await gw_client.post(f"/internal/v1/models/{QUALITY}/load")
    assert low.status_code == 409 and "availableBytes" in low.json()["error"]["details"]
    async with gateway.sessions() as db:
        instance = await db.get(ModelInstance, QUALITY)
        assert instance.state == "NOT_LOADED" and instance.reserved_bytes == 0


# --- failures -------------------------------------------------------------------------------------


async def test_load_failure_then_retry(gw_client: httpx.AsyncClient, gateway: Gateway) -> None:
    await install(gw_client)
    runtime: InProcessModelRuntime = gateway.runtime  # type: ignore[assignment]
    runtime.fail_start.add(SMALL)
    failed = await gw_client.post("/internal/v1/llm/chat", json=chat_body())
    assert failed.status_code == 503 and failed.json()["error"]["code"] == "MODEL_LOAD_FAILED"
    model = (await gw_client.get(f"/internal/v1/models/{SMALL}")).json()
    assert model["memoryState"] == "LOAD_ERROR" and model["reservedBytes"] == 0
    runtime.fail_start.clear()
    assert (await gw_client.post("/internal/v1/llm/chat", json=chat_body())).status_code == 200


async def test_crash_is_detected_and_reloaded(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client)
    assert (await gw_client.post("/internal/v1/llm/chat", json=chat_body())).status_code == 200
    runtime: InProcessModelRuntime = gateway.runtime  # type: ignore[assignment]
    runtime.crash.add(SMALL)
    report = await gateway.manager.reap()
    assert report["crashed"] == 1
    model = (await gw_client.get(f"/internal/v1/models/{SMALL}")).json()
    assert model["memoryState"] == "ERROR" and model["error"]["oomKilled"] is True
    again = await gw_client.post("/internal/v1/llm/chat", json=chat_body())
    assert again.status_code == 200
    assert runtime.starts == [SMALL, SMALL]


async def test_manual_unload_respects_leases(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client)
    await gw_client.post("/internal/v1/llm/chat", json=chat_body())
    in_use = await gw_client.post(f"/internal/v1/models/{SMALL}/unload", json={})
    assert in_use.status_code == 409 and in_use.json()["error"]["details"]["holders"] == [
        "Chat: My chat"
    ]
    forced = await gw_client.post(f"/internal/v1/models/{SMALL}/unload", json={"force": True})
    assert forced.json()["memoryState"] == "DRAINING"
    blocked = await gw_client.post("/internal/v1/llm/chat", json=chat_body())
    assert blocked.status_code in (409, 503)
    for _ in range(100):
        if (await gw_client.get(f"/internal/v1/models/{SMALL}")).json()[
            "memoryState"
        ] == "NOT_LOADED":
            break
        await asyncio.sleep(0.05)
    assert (await gw_client.get(f"/internal/v1/models/{SMALL}")).json()[
        "memoryState"
    ] == "NOT_LOADED"
    busy = await gw_client.delete(f"/internal/v1/models/{QUALITY}/files")
    assert busy.status_code == 200


async def test_new_lease_cancels_idle_drain(gw_client: httpx.AsyncClient, gateway: Gateway) -> None:
    await install(gw_client)
    await gw_client.post("/internal/v1/llm/chat", json=chat_body())
    await gw_client.delete("/internal/v1/leases/holders/chat/session-1")
    async with gateway.sessions() as db, db.begin():
        instance = await db.get(ModelInstance, SMALL, with_for_update=True)
        instance.state, instance.drain_reason = "DRAINING", "idle"
    await gateway.manager.acquire(SMALL, "chat", "s2", "c", 60)
    async with gateway.sessions() as db:
        assert (await db.get(ModelInstance, SMALL)).state == "READY"


async def test_expired_leases_are_released(gw_client: httpx.AsyncClient, gateway: Gateway) -> None:
    await install(gw_client)
    await gw_client.post("/internal/v1/llm/chat", json=chat_body())
    async with gateway.sessions() as db, db.begin():
        await db.execute(text("UPDATE model_leases SET expires_at = now() - interval '1 second'"))
    assert (await gateway.manager.reap())["expired"] == 1
    async with gateway.sessions() as db:
        lease = await db.scalar(select(ModelLease))
        assert lease.release_reason == "expired"
        assert (await db.get(ModelInstance, SMALL)).idle_since is not None


# --- runs -----------------------------------------------------------------------------------------


async def test_run_calls_require_active_attempt_and_capability(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client)
    run_id = str(uuid.uuid4())
    token, _ = run_token(gateway, run_id)
    body = {"profile": "local.general", "messages": [{"role": "user", "content": "hi"}]}
    ok = await gw_client.post(
        "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": token}
    )
    assert ok.status_code == 200, ok.text
    control: FakeControl = gateway.control  # type: ignore[assignment]
    assert control.signals == [(run_id, True), (run_id, False)]  # LOADING_MODEL while cold

    denied = await gw_client.post(
        "/internal/v1/llm/chat",
        json={**body, "profile": QUALITY},
        headers={"X-Capability-Token": token},
    )
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "CAPABILITY_DENIED"
    assert denied.json()["error"]["details"] == {"capability": f"llm.profile:{QUALITY}"}

    control.runs[run_id]["currentAttempt"] = 2  # a retry made this token stale
    stale = await gw_client.post(
        "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": token}
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "RUN_NOT_ACTIVE"
    assert stale.json()["error"]["requestId"]  # every error envelope carries one

    forged = await gw_client.post(
        "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": token + "x"}
    )
    assert forged.status_code == 401 and forged.json()["error"]["code"] == "UNAUTHENTICATED"

    chat_cannot_impersonate = await gw_client.post("/internal/v1/llm/chat", json=body)
    assert chat_cannot_impersonate.status_code == 401


async def test_run_leases_release_when_run_finishes(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client)
    run_id = str(uuid.uuid4())
    token, _ = run_token(gateway, run_id)
    await gw_client.post(
        "/internal/v1/llm/chat",
        json={"profile": "local.general", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-Capability-Token": token},
    )
    control: FakeControl = gateway.control  # type: ignore[assignment]
    control.runs[run_id]["state"] = "SUCCEEDED"
    assert (await gateway.manager.reap(control.run_is_active))["runReleased"] == 1


async def test_per_run_token_budget(gw_client: httpx.AsyncClient, gateway: Gateway) -> None:
    await install(gw_client)
    # Each request reserves prompt/4 + maxOutputTokens (3 + 10) before it runs.
    gateway.settings.per_run_token_limit = 20
    run_id = str(uuid.uuid4())
    token, _ = run_token(gateway, run_id)
    body = {
        "profile": SMALL,
        "messages": [{"role": "user", "content": "one two three"}],
        "maxOutputTokens": 10,
    }
    first = await gw_client.post(
        "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": token}
    )
    assert first.status_code == 200, first.text
    second = await gw_client.post(
        "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": token}
    )
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "RUN_TOKEN_BUDGET_EXCEEDED"


# --- cloud ----------------------------------------------------------------------------------------


async def test_cloud_requires_explicit_permission_and_credentials(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    run_id = str(uuid.uuid4())
    local_only, _ = run_token(gateway, run_id)
    body = {"profile": "anthropic.default", "messages": [{"role": "user", "content": "hi"}]}
    denied = await gw_client.post(
        "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": local_only}
    )
    assert denied.status_code == 403
    cloud_run = str(uuid.uuid4())
    cloud, _ = run_token(
        gateway, cloud_run, caps=["cloud.anthropic", "llm.profile:anthropic.default"]
    )
    missing = await gw_client.post(
        "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": cloud}
    )
    assert missing.status_code == 409 and missing.json()["error"]["code"] == "NEEDS_CONNECTION"
    openai_body = {**body, "profile": "openai.default"}
    openai_run = str(uuid.uuid4())
    oai, _ = run_token(gateway, openai_run, caps=["cloud.openai", "llm.profile:openai.default"])
    unconfigured = await gw_client.post(
        "/internal/v1/llm/chat", json=openai_body, headers={"X-Capability-Token": oai}
    )
    assert unconfigured.json()["error"]["code"] == "NEEDS_CONFIGURATION"
    chat_cloud = await gw_client.post(
        "/internal/v1/llm/chat", json={**body, "holder": {"type": "chat", "id": "c"}}
    )
    assert chat_cloud.status_code == 403  # chat is local-only


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
    assert err.value.code == "RATE_LIMITED" and err.value.status_code == 429


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
    gw_client: httpx.AsyncClient, gateway: Gateway
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
    mock_cloud(gateway, anthropic=handler)
    run_id = str(uuid.uuid4())
    token, _ = run_token(gateway, run_id, caps=["cloud.anthropic", "llm.profile:anthropic.default"])
    response = await gw_client.post(
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


async def test_unmocked_provider_calls_cannot_leave_the_machine() -> None:
    adapter = AnthropicAdapter("sk-test", "claude-opus-5", 5)
    with pytest.raises(PlatformError) as err:
        await adapter.chat(
            ChatRequest(messages=[{"role": "user", "content": "x"}], max_output_tokens=5)
        )
    assert err.value.code == "PROVIDER_UNAVAILABLE"  # connection refused locally, not a 401
