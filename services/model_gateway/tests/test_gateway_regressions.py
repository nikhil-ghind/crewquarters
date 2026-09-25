"""Regression tests for the model-gateway review findings."""

# ruff: noqa: F811 - the imported pytest fixtures are re-declared as test parameters

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import httpx
import httpx2
from sqlalchemy import select
from test_gateway import (  # type: ignore[import-not-found]
    QUALITY,
    SMALL,
    anthropic_client,
    chat_body,
    client,  # noqa: F401 - fixture
    gateway,  # noqa: F401 - fixture
    install,
    run_token,
)

from crewquarters_gateway.credentials import StaticCredentials
from crewquarters_gateway.main import Gateway
from crewquarters_gateway.runtime import InProcessModelRuntime
from crewquarters_shared.db.models_gateway import LlmUsage, ModelInstance, ModelLease
from crewquarters_shared.errors import PlatformError
from crewquarters_shared.timeutil import utcnow


async def test_concurrent_admission_of_two_models_is_serialized(
    client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(client)
    await install(client, QUALITY)
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
    from test_gateway import FakeControl, gateway_settings  # type: ignore[import-not-found]

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
    client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(client)
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


async def test_orphan_server_is_stopped(client: httpx.AsyncClient, gateway: Gateway) -> None:
    runtime: InProcessModelRuntime = gateway.runtime  # type: ignore[assignment]
    runtime.running.add(SMALL)  # e.g. a start that completed after a timed-out load
    report = await gateway.manager.reap()
    assert report["orphansStopped"] == 1 and SMALL not in runtime.running


async def test_stream_errors_are_http_errors_not_truncated_streams(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/internal/v1/llm/chat", json=chat_body(stream=True))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_NOT_INSTALLED"


async def test_chat_requires_the_control_api_credential(client: httpx.AsyncClient) -> None:
    await install(client)
    no_chat_token = {"X-Chat-Client-Token": ""}
    response = await client.post("/internal/v1/llm/chat", json=chat_body(), headers=no_chat_token)
    assert response.status_code == 401
    lease = await client.post(
        "/internal/v1/leases",
        json={"modelId": SMALL, "holderType": "chat", "holderId": "x"},
        headers=no_chat_token,
    )
    assert lease.status_code == 401
    empty_capability = await client.post(
        "/internal/v1/llm/chat", json=chat_body(), headers={"X-Capability-Token": ""}
    )
    assert empty_capability.status_code == 401  # a present header means "run caller"


async def test_parallel_requests_cannot_overshoot_the_run_budget(
    client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(client)
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
            client.post("/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": token})
            for _ in range(3)
        )
    )
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1 and codes.count(429) == 2, codes


async def test_invalid_structured_output_still_counts_usage(
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
                "content": [{"type": "text", "text": "not json"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 40, "output_tokens": 30},
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
                "content": [{"type": "text", "text": "partial"}],
                "stop_reason": "refusal",
                "stop_sequence": None,
                "stop_details": {"type": "refusal", "category": "bio", "explanation": None},
                "usage": {"input_tokens": 9, "output_tokens": 4},
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
    client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(client)
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
    client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(client)
    await client.post("/internal/v1/llm/chat", json=chat_body())
    await client.delete("/internal/v1/leases/holders/chat/session-1")
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
    client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(client)
    await client.post("/internal/v1/llm/chat", json=chat_body())
    memory = (await client.get("/internal/v1/memory")).json()
    assert memory["reservedBytes"] == 2 * 1024**3 and memory["models"][0]["id"] == SMALL
    resident = await client.delete(f"/internal/v1/models/{SMALL}/files")
    assert resident.status_code == 409 and resident.json()["error"]["code"] == "MODEL_RESIDENT"
    cancelled = await client.post(
        f"/internal/v1/models/{QUALITY}/install/cancel", json={"clear": True}
    )
    assert cancelled.json()["downloadState"] == "NOT_INSTALLED"
    metrics = await client.get("/internal/v1/metrics")
    assert 'cq_model_resident{model="local.general.small"} 1.0' in metrics.text
