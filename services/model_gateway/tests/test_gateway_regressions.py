"""Regression tests for the model-gateway review findings."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import httpx
import httpx2
from gateway_helpers import (
    QUALITY,
    SMALL,
    FakeControl,
    chat_body,
    gateway_settings,
    install,
    mock_cloud,
    run_token,
)
from sqlalchemy import select

from crewquarters_gateway.credentials import StaticCredentials
from crewquarters_gateway.main import Gateway
from crewquarters_gateway.runtime import InProcessModelRuntime
from crewquarters_shared.db.models_gateway import LlmUsage, ModelInstance, ModelLease
from crewquarters_shared.errors import PlatformError
from crewquarters_shared.timeutil import utcnow


async def test_concurrent_admission_of_two_models_is_serialized(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client)
    await install(gw_client, QUALITY)
    results = await asyncio.gather(
        gateway.manager.acquire(SMALL, "chat", "a", "a", 60),
        gateway.manager.acquire(QUALITY, "chat", "b", "b", 60),
        return_exceptions=True,
    )
    refused = [r for r in results if isinstance(r, PlatformError)]
    assert len(refused) == 1 and refused[0].code == "MODEL_CAPACITY_EXCEEDED"


class SlowStopRuntime(InProcessModelRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.stopping = asyncio.Event()
        self.release = asyncio.Event()

    async def stop(self, model_id: str) -> dict[str, Any]:
        self.stopping.set()
        await self.release.wait()
        return await super().stop(model_id)


async def test_lease_during_idle_stop_reloads_instead_of_dying(settings) -> None:  # type: ignore[no-untyped-def]
    runtime = SlowStopRuntime()
    gw = Gateway(
        settings, gateway_settings(idle_unload_seconds=0), runtime=runtime, control=FakeControl()
    )  # type: ignore[arg-type]
    await gw.sync_catalog()
    stop = asyncio.Event()
    worker = asyncio.create_task(gw.worker_loop(stop))
    try:
        await gw.manager.install(SMALL)
        await gw.manager.acquire(SMALL, "chat", "first", "first", 60)
        await gw.manager.wait_ready(SMALL)
        await gw.manager.release_holder("chat", "first", "done")
        await gw.manager.reap()
        await gw.manager.reap()  # idle -> DRAINING(idle) -> unload job starts stopping
        await asyncio.wait_for(runtime.stopping.wait(), 5)
        async with gw.sessions() as db:
            assert (await db.get(ModelInstance, SMALL)).drain_reason == "stopping"
        await gw.manager.acquire(SMALL, "chat", "late", "late", 60)  # arrives mid-stop
        runtime.release.set()
        endpoint = await gw.manager.wait_ready(SMALL, 10)
        assert endpoint.served_model == SMALL
        async with gw.sessions() as db:
            instance = await db.get(ModelInstance, SMALL)
            lease = await db.scalar(select(ModelLease).where(ModelLease.holder_id == "late"))
        assert instance.state == "READY" and lease.released_at is None
        assert runtime.starts == [SMALL, SMALL]
    finally:
        stop.set()
        await worker
        await gw.close()


async def test_stuck_loading_without_a_job_is_recovered(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client)
    async with gateway.sessions() as db, db.begin():
        instance = await db.get(ModelInstance, SMALL, with_for_update=True)
        instance.state, instance.reserved_bytes = "LOADING", 123
        instance.load_started_at = utcnow() - timedelta(minutes=10)
    report = await gateway.manager.reap()
    assert report["recovered"] == 1
    async with gateway.sessions() as db:
        instance = await db.get(ModelInstance, SMALL)
    assert instance.state == "LOAD_ERROR" and instance.reserved_bytes == 0
    assert instance.error["code"] == "LOAD_INTERRUPTED"


async def test_orphan_server_is_stopped(gw_client: httpx.AsyncClient, gateway: Gateway) -> None:
    runtime: InProcessModelRuntime = gateway.runtime  # type: ignore[assignment]
    runtime.running.add(SMALL)  # e.g. a start that completed after a timed-out load
    report = await gateway.manager.reap()
    assert report["orphansStopped"] == 1 and SMALL not in runtime.running


async def test_stream_errors_are_http_errors_not_truncated_streams(
    gw_client: httpx.AsyncClient,
) -> None:
    response = await gw_client.post("/internal/v1/llm/chat", json=chat_body(stream=True))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_NOT_INSTALLED"


async def test_chat_requires_the_control_api_credential(gw_client: httpx.AsyncClient) -> None:
    await install(gw_client)
    no_chat_token = {"X-Chat-Client-Token": ""}
    response = await gw_client.post(
        "/internal/v1/llm/chat", json=chat_body(), headers=no_chat_token
    )
    assert response.status_code == 401
    lease = await gw_client.post(
        "/internal/v1/leases",
        json={"modelId": SMALL, "holderType": "chat", "holderId": "x"},
        headers=no_chat_token,
    )
    assert lease.status_code == 401
    empty_capability = await gw_client.post(
        "/internal/v1/llm/chat", json=chat_body(), headers={"X-Capability-Token": ""}
    )
    assert empty_capability.status_code == 401  # a present header means "run caller"


async def test_parallel_requests_cannot_overshoot_the_run_budget(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client)
    gateway.settings.per_run_token_limit = 80  # one request reserves ~50 + prompt
    run_id = str(uuid.uuid4())
    token, _ = run_token(gateway, run_id)
    body = {
        "profile": SMALL,
        "messages": [{"role": "user", "content": "hi"}],
        "maxOutputTokens": 50,
    }
    results = await asyncio.gather(
        *(
            gw_client.post(
                "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": token}
            )
            for _ in range(3)
        )
    )
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1 and codes.count(429) == 2, codes


async def test_invalid_structured_output_still_counts_usage(
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
                "content": [{"type": "text", "text": "not json"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 40, "output_tokens": 30},
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
            "messages": [{"role": "user", "content": "x"}],
            "responseSchema": {"type": "object", "required": ["a"]},
        },
        headers={"X-Capability-Token": token},
    )
    assert (
        response.status_code == 502
        and response.json()["error"]["code"] == "STRUCTURED_OUTPUT_INVALID"
    )
    async with gateway.sessions() as db:
        usage = await db.scalar(select(LlmUsage))
    assert (
        usage.input_tokens == 40 and usage.output_tokens == 30 and usage.outcome == "invalid_output"
    )


async def test_refusal_records_usage_then_fails(
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
                "content": [{"type": "text", "text": "partial"}],
                "stop_reason": "refusal",
                "stop_sequence": None,
                "stop_details": {"type": "refusal", "category": "bio", "explanation": None},
                "usage": {"input_tokens": 9, "output_tokens": 4},
            },
        )

    gateway.inference.credentials = StaticCredentials({"anthropic": "sk-test"})
    mock_cloud(gateway, anthropic=handler)
    run_id = str(uuid.uuid4())
    token, _ = run_token(gateway, run_id, caps=["cloud.anthropic", "llm.profile:anthropic.default"])
    response = await gw_client.post(
        "/internal/v1/llm/chat",
        json={"profile": "anthropic.default", "messages": [{"role": "user", "content": "x"}]},
        headers={"X-Capability-Token": token},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MODEL_REFUSED"
    assert response.json()["error"]["details"]["category"] == "bio"
    async with gateway.sessions() as db:
        usage = await db.scalar(select(LlmUsage))
    assert usage.output_tokens == 4 and usage.outcome == "refused"


async def test_restart_during_load_adopts_a_healthy_server(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client)
    runtime: InProcessModelRuntime = gateway.runtime  # type: ignore[assignment]
    runtime.running.add(SMALL)  # the server came up but the gateway restarted mid-load
    async with gateway.sessions() as db, db.begin():
        instance = await db.get(ModelInstance, SMALL, with_for_update=True)
        instance.state, instance.reserved_bytes = "LOADING", 2
        instance.load_started_at = utcnow() - timedelta(minutes=10)
    assert (await gateway.manager.reap())["recovered"] == 1
    async with gateway.sessions() as db:
        instance = await db.get(ModelInstance, SMALL)
    assert instance.state == "READY" and instance.endpoint["inprocess"] is True


async def test_stuck_drain_without_a_job_is_requeued(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client)
    await gw_client.post("/internal/v1/llm/chat", json=chat_body())
    await gw_client.delete("/internal/v1/leases/holders/chat/session-1")
    async with gateway.sessions() as db, db.begin():
        instance = await db.get(ModelInstance, SMALL, with_for_update=True)
        instance.state, instance.drain_reason = "DRAINING", "idle"
    assert (await gateway.manager.reap())["recovered"] == 1
    for _ in range(100):
        async with gateway.sessions() as db:
            if (await db.get(ModelInstance, SMALL)).state == "NOT_LOADED":
                break
        await asyncio.sleep(0.05)
    async with gateway.sessions() as db:
        assert (await db.get(ModelInstance, SMALL)).state == "NOT_LOADED"


async def test_memory_view_and_resident_delete_guard(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client)
    await gw_client.post("/internal/v1/llm/chat", json=chat_body())
    memory = (await gw_client.get("/internal/v1/memory")).json()
    assert memory["reservedBytes"] == 2 * 1024**3 and memory["models"][0]["id"] == SMALL
    resident = await gw_client.delete(f"/internal/v1/models/{SMALL}/files")
    assert resident.status_code == 409 and resident.json()["error"]["code"] == "MODEL_RESIDENT"
    cancelled = await gw_client.post(
        f"/internal/v1/models/{QUALITY}/install/cancel", json={"clear": True}
    )
    assert cancelled.json()["downloadState"] == "NOT_INSTALLED"
    metrics = await gw_client.get("/internal/v1/metrics")
    assert 'cq_model_resident{model="local.general.small"} 1.0' in metrics.text


class ScriptedAdapter:
    """Adapter whose stream follows a script: deltas, then a failure or a result."""

    provider = "anthropic"

    def __init__(self, deltas: list[str], end: Any) -> None:
        self.deltas = deltas
        self.end = end

    async def chat(self, request: Any) -> Any:  # pragma: no cover - not used
        raise AssertionError

    async def stream(self, request: Any) -> Any:
        for text in self.deltas:
            yield {"type": "delta", "text": text}
        if isinstance(self.end, Exception):
            raise self.end
        yield {"type": "result", "result": self.end}


def prepared_with(gateway: Gateway, adapter: ScriptedAdapter) -> Any:
    from crewquarters_gateway.adapters import ChatRequest
    from crewquarters_gateway.inference import Caller, Prepared

    return Prepared(
        caller=Caller("chat", "stream-test", "Stream test"),
        request=ChatRequest(
            messages=[{"role": "user", "content": "x" * 400}], max_output_tokens=50
        ),
        profile="anthropic.default",
        provider="anthropic",
        target="claude-opus-5",
        reserved=0,
        adapter=adapter,  # type: ignore[arg-type]
    )


async def usage_rows(gateway: Gateway) -> list[LlmUsage]:
    async with gateway.sessions() as db:
        return list((await db.scalars(select(LlmUsage))).all())


async def test_mid_stream_error_records_partial_usage(gateway: Gateway) -> None:
    failure = PlatformError("PROVIDER_UNAVAILABLE", "connection reset", 502)
    events = [
        e
        async for e in gateway.inference.stream(
            prepared_with(gateway, ScriptedAdapter(["a" * 40, "b" * 40], failure))
        )
    ]
    assert [e["type"] for e in events] == ["delta", "delta", "error"]
    assert events[-1]["error"]["code"] == "PROVIDER_UNAVAILABLE"
    (row,) = await usage_rows(gateway)
    assert (
        row.outcome == "PROVIDER_UNAVAILABLE"
        and row.output_tokens == 20
        and row.input_tokens == 100
    )


async def test_refusal_inside_a_stream_is_an_error_event(gateway: Gateway) -> None:
    from crewquarters_gateway.adapters import ChatResult

    refused = ChatResult(
        text="",
        finish_reason="refusal",
        provider="anthropic",
        model="claude-opus-5",
        input_tokens=5,
        output_tokens=2,
        refusal_category="cyber",
    )
    events = [
        e
        async for e in gateway.inference.stream(
            prepared_with(gateway, ScriptedAdapter(["x"], refused))
        )
    ]
    assert events[-1] == {
        "type": "error",
        "error": {
            "code": "MODEL_REFUSED",
            "message": "The model declined this request.",
            "details": {"category": "cyber", "provider": "anthropic"},
        },
    }
    (row,) = await usage_rows(gateway)
    assert row.outcome == "refused" and row.output_tokens == 2


async def test_cancelled_stream_records_estimated_usage(gateway: Gateway) -> None:
    stream = gateway.inference.stream(
        prepared_with(gateway, ScriptedAdapter(["c" * 80, "d" * 80], PlatformError("X", "y", 502)))
    )
    first = await anext(stream)
    assert first["type"] == "delta"
    await stream.aclose()  # the client disconnected (Stop)
    (row,) = await usage_rows(gateway)
    assert row.outcome == "cancelled" and row.output_tokens == 20
    assert gateway.manager.inflight["claude-opus-5"] == 0
