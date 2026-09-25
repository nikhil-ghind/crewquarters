"""Idempotent /llm/chat, broker-SDK contract alignment of responses and errors, and the
timeout chain SDK > broker > gateway."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
import yaml
from gateway_helpers import ROOT, chat_body, install, mock_cloud, run_token
from jsonschema import Draft202012Validator
from sqlalchemy import func, select

from crewquarters_gateway.adapters import normalize_finish_reason
from crewquarters_gateway.config import GatewaySettings
from crewquarters_gateway.credentials import StaticCredentials
from crewquarters_gateway.idempotency import FinalError, IdempotencyStore
from crewquarters_gateway.main import Gateway
from crewquarters_shared.db.models_gateway import LlmUsage
from crewquarters_shared.errors import PlatformError

CONTRACT = ROOT / "packages/contracts/broker-sdk.openapi.yaml"


def contract_validator(name: str) -> Draft202012Validator:
    document = yaml.safe_load(Path(CONTRACT).read_text())
    return Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": document["components"]}
    )


async def usage_rows(gateway: Gateway) -> int:
    async with gateway.sessions() as db:
        return int(await db.scalar(select(func.count()).select_from(LlmUsage)) or 0)


def slow_chat(gateway: Gateway, delay: float = 0.2) -> list[int]:
    """Count real executions of the non-streaming chat and make each take a while."""
    calls: list[int] = []
    original = gateway.inference.chat

    async def chat(caller: Any, body: dict[str, Any]) -> dict[str, Any]:
        calls.append(1)
        await asyncio.sleep(delay)
        return await original(caller, body)

    gateway.inference.chat = chat  # type: ignore[method-assign]
    return calls


# --- idempotency: through the route ----------------------------------------------------------


async def test_concurrent_duplicates_wait_for_the_first(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client)
    calls = slow_chat(gateway)
    body = chat_body(idempotencyKey="digest-final-v1")
    responses = await asyncio.gather(
        *[gw_client.post("/internal/v1/llm/chat", json=body) for _ in range(4)]
    )
    assert [r.status_code for r in responses] == [200] * 4, [r.text for r in responses]
    assert len({r.text for r in responses}) == 1  # identical, including requestId
    assert len(calls) == 1 and await usage_rows(gateway) == 1

    later = await gw_client.post("/internal/v1/llm/chat", json=body)  # replayed from the store
    assert later.json() == responses[0].json() and len(calls) == 1

    other_key = await gw_client.post("/internal/v1/llm/chat", json=chat_body(idempotencyKey="v2"))
    unkeyed = await gw_client.post("/internal/v1/llm/chat", json=chat_body())
    assert other_key.status_code == unkeyed.status_code == 200
    assert len(calls) == 3 and await usage_rows(gateway) == 3


async def test_key_reuse_with_a_different_request_is_rejected(
    gw_client: httpx.AsyncClient,
) -> None:
    await install(gw_client)
    first = await gw_client.post("/internal/v1/llm/chat", json=chat_body(idempotencyKey="k1"))
    assert first.status_code == 200
    changed = chat_body(idempotencyKey="k1", maxOutputTokens=51)
    reused = await gw_client.post("/internal/v1/llm/chat", json=changed)
    assert reused.status_code == 422 and reused.json()["error"]["code"] == "INVALID_REQUEST"
    # Another holder may use the same key independently.
    other = chat_body(idempotencyKey="k1", holder={"type": "chat", "id": "session-2"})
    assert (await gw_client.post("/internal/v1/llm/chat", json=other)).status_code == 200


async def test_keys_are_scoped_to_the_run(gw_client: httpx.AsyncClient, gateway: Gateway) -> None:
    await install(gw_client)
    calls = slow_chat(gateway, 0)
    body = {
        "profile": "local.general",
        "messages": [{"role": "user", "content": "hi"}],
        "idempotencyKey": "same",
    }
    for _ in range(2):
        token, _ = run_token(gateway, str(uuid.uuid4()))
        response = await gw_client.post(
            "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": token}
        )
        assert response.status_code == 200
    assert len(calls) == 2


async def test_transient_failures_are_not_stored(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client)
    original = gateway.inference.chat
    outcomes = [PlatformError("PROVIDER_UNAVAILABLE", "down", 503)]

    async def flaky(caller: Any, body: dict[str, Any]) -> dict[str, Any]:
        if outcomes:
            raise outcomes.pop()
        return await original(caller, body)

    gateway.inference.chat = flaky  # type: ignore[method-assign]
    body = chat_body(idempotencyKey="retry-me")
    failed = await gw_client.post("/internal/v1/llm/chat", json=body)
    assert failed.status_code == 503
    retried = await gw_client.post("/internal/v1/llm/chat", json=body)
    assert retried.status_code == 200 and retried.json()["text"] == "Mock reply to: hello crew"


async def test_billed_final_errors_are_replayed_not_repeated(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(
            200,
            json={
                "id": "m",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [{"type": "text", "text": "not json"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 4, "output_tokens": 3},
            },
        )

    gateway.inference.credentials = StaticCredentials({"anthropic": "sk-test"})
    mock_cloud(gateway, anthropic=handler)
    token, _ = run_token(
        gateway, str(uuid.uuid4()), caps=["cloud.anthropic", "llm.profile:anthropic.default"]
    )
    body = {
        "profile": "anthropic.default",
        "messages": [{"role": "user", "content": "x"}],
        "responseSchema": {"type": "object", "required": ["a"]},
        "idempotencyKey": "structured-1",
    }
    headers = {"X-Capability-Token": token}
    first = await gw_client.post("/internal/v1/llm/chat", json=body, headers=headers)
    retry = await gw_client.post("/internal/v1/llm/chat", json=body, headers=headers)
    assert first.status_code == retry.status_code == 502
    assert retry.json()["error"]["code"] == "STRUCTURED_OUTPUT_INVALID"
    assert len(calls) == 1 and await usage_rows(gateway) == 1


async def test_streams_with_a_key_in_flight_reject_duplicates(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client)
    original = gateway.inference.stream
    release = asyncio.Event()

    async def slow_stream(prepared: Any) -> Any:
        await release.wait()
        async for event in original(prepared):
            yield event

    gateway.inference.stream = slow_stream  # type: ignore[method-assign]
    body = chat_body(idempotencyKey="stream-1", stream=True)
    # httpx's ASGI transport returns only after the whole stream, so run it as a task.
    first = asyncio.create_task(gw_client.post("/internal/v1/llm/chat", json=body))
    for _ in range(200):
        if len(gateway.idempotency):
            break
        await asyncio.sleep(0.01)
    duplicate = await gw_client.post("/internal/v1/llm/chat", json=body)
    plain = await gw_client.post("/internal/v1/llm/chat", json={**body, "stream": False})
    release.set()
    finished = await first
    assert finished.status_code == 200 and "event: done" in finished.text
    assert duplicate.status_code == plain.status_code == 409
    assert duplicate.json()["error"]["code"] == "REQUEST_IN_PROGRESS"
    # Released when the stream ends (streams are never replayed).
    assert len(gateway.idempotency) == 0
    again = await gw_client.post("/internal/v1/llm/chat", json=body)
    assert again.status_code == 200 and "event: done" in again.text


async def test_failed_stream_preparation_releases_the_key(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    body = chat_body(idempotencyKey="stream-2", stream=True)
    not_installed = await gw_client.post("/internal/v1/llm/chat", json=body)
    assert not_installed.status_code == 409
    assert not_installed.json()["error"]["code"] == "MODEL_NOT_INSTALLED"
    assert len(gateway.idempotency) == 0


# --- idempotency: the store -----------------------------------------------------------------


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_store_expires_and_bounds_completed_entries() -> None:
    clock = Clock()
    store = IdempotencyStore(ttl_seconds=10, max_entries=2, clock=clock)
    runs: list[str] = []

    def call(value: str) -> Any:
        async def run() -> dict[str, Any]:
            runs.append(value)
            return {"v": value}

        return run

    key = ("run", "r", "k")
    assert await store.run(key, {"a": 1}, call("one")) == {"v": "one"}
    assert await store.run(key, {"a": 1}, call("two")) == {"v": "one"}
    clock.now = 11
    assert await store.run(key, {"a": 1}, call("three")) == {"v": "three"}
    for i in range(3):
        await store.run(("run", "r", f"other{i}"), {}, call(f"o{i}"))
    assert len(store) <= 2 and runs == ["one", "three", "o0", "o1", "o2"]


async def test_store_waiters_get_a_retryable_error_when_the_first_is_cancelled() -> None:
    store = IdempotencyStore(ttl_seconds=10, max_entries=10)
    started = asyncio.Event()

    async def hang() -> dict[str, Any]:
        started.set()
        await asyncio.sleep(10)
        return {}

    first = asyncio.create_task(store.run(("chat", "c", "k"), {}, hang))
    await started.wait()
    waiter = asyncio.create_task(store.run(("chat", "c", "k"), {}, hang))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(PlatformError) as err:
        await waiter
    assert err.value.code == "MODEL_UNAVAILABLE" and err.value.status_code == 503
    assert len(store) == 0  # the next attempt runs again


async def test_store_replays_final_errors_only() -> None:
    store = IdempotencyStore(ttl_seconds=10, max_entries=10)

    async def refused() -> dict[str, Any]:
        raise FinalError("MODEL_REFUSED", "no", 422)

    for _ in range(2):
        with pytest.raises(PlatformError) as err:
            await store.run(("run", "r", "k"), {}, refused)
        assert err.value.code == "MODEL_REFUSED"
    assert len(store) == 1


# --- contract alignment -----------------------------------------------------------------------


async def test_chat_response_matches_the_broker_sdk_contract(
    gw_client: httpx.AsyncClient,
) -> None:
    await install(gw_client)
    validator = contract_validator("ChatResponse")
    response = await gw_client.post(
        "/internal/v1/llm/chat", json=chat_body(), headers={"X-Request-Id": "req-abcdef-123"}
    )
    body = response.json()
    assert list(validator.iter_errors(body)) == []
    assert body["locality"] == "local" and body["finishReason"] == "stop"
    # The mock server has no request id of its own: the gateway's is used and echoed.
    assert body["requestId"] == response.headers["x-request-id"] == "req-abcdef-123"

    async with gw_client.stream(
        "POST", "/internal/v1/llm/chat", json=chat_body(stream=True)
    ) as stream:
        events = [
            json.loads(line[6:]) async for line in stream.aiter_lines() if line.startswith("data: ")
        ]
    done = events[-1]["response"]
    assert list(validator.iter_errors(done)) == [] and done["requestId"]


async def test_error_envelopes_match_the_contract(gw_client: httpx.AsyncClient) -> None:
    validator = contract_validator("Error")
    response = await gw_client.post("/internal/v1/llm/chat", json=chat_body())
    body = response.json()
    assert response.status_code == 409 and list(validator.iter_errors(body)) == []
    assert body["error"]["requestId"] == response.headers["x-request-id"]
    assert len(body["error"]["requestId"]) >= 8
    unknown_route = await gw_client.get("/internal/v1/nope")
    assert unknown_route.status_code == 404
    assert unknown_route.json()["error"]["code"] == "NOT_FOUND"
    bogus = await gw_client.post(
        "/internal/v1/llm/chat", json=chat_body(), headers={"X-Request-Id": "bad id\x7f"}
    )
    assert bogus.json()["error"]["requestId"] != "bad id\x7f"


def test_finish_reasons_use_the_contract_enum() -> None:
    cases = {
        None: "stop",
        "stop": "stop",
        "end_turn": "stop",
        "length": "length",
        "max_tokens": "length",
        "max_output_tokens": "length",
        "content_filter": "content_filter",
        "tool_use": "error",
        "abort": "error",
    }
    for raw, expected in cases.items():
        assert normalize_finish_reason(raw) == expected


def test_provider_errors_use_contract_codes() -> None:
    from crewquarters_gateway.adapters import provider_status_error

    assert (provider_status_error("X", 429).code, provider_status_error("X", 429).status_code) == (
        "RATE_LIMITED",
        429,
    )
    assert provider_status_error("X", 400).code == "PROVIDER_ERROR"
    assert provider_status_error("X", 400).status_code == 502
    assert provider_status_error("X", 500).code == "PROVIDER_UNAVAILABLE"
    assert provider_status_error("X", 500).status_code == 503


# --- timeouts -----------------------------------------------------------------------------------


def test_each_layer_waits_longer_than_the_one_inside_it() -> None:
    from crewquarters.llm import LLM_TIMEOUT_SECONDS
    from crewquarters_broker.main import GATEWAY_TIMEOUT_SECONDS

    gateway = GatewaySettings()
    gateway_worst = gateway.wait_ready_seconds + gateway.request_timeout_seconds
    assert gateway_worst < GATEWAY_TIMEOUT_SECONDS < LLM_TIMEOUT_SECONDS
    # PLAN.md section 8: a cold local model may take about ten minutes to load.
    assert gateway.wait_ready_seconds >= 600
    # The DGX catalog's load timeout fits inside the gateway's readiness wait.
    for profile in (ROOT / "catalog/models/dgx").glob("*.json"):
        startup = json.loads(profile.read_text())["launch"]["startupTimeoutSeconds"]
        assert startup <= gateway.wait_ready_seconds, profile.name
