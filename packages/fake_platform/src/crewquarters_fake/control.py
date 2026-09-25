"""The fake's slice of the control API (/api/v1), mirroring ``packages/contracts/openapi.yaml``.

Only the operations the SDK tools, harness, and demo need are served; paths, bodies, status codes,
and response shapes match the canonical control API. The fake requires no login.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from crewquarters_fake import services
from crewquarters_fake.statemachine import SETTLED
from crewquarters_fake.store import CatalogEntry, Store
from crewquarters_fake.views import (
    catalog_view,
    event_view,
    input_control_view,
    installation_view,
    page,
    run_view,
)

router = APIRouter(prefix="/api/v1")


def store_of(request: Request) -> Store:
    store: Store = request.app.state.store
    return store


class ImportIn(BaseModel):
    manifest: dict[str, Any]


class InstallIn(BaseModel):
    agentId: str
    version: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    approvedPermissions: dict[str, Any]
    modelBindings: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class RunIn(BaseModel):
    installationId: str


class AnswerIn(BaseModel):
    version: int
    value: Any = None


@router.post("/catalog/agents/import", status_code=201)
async def import_agent(body: ImportIn, request: Request) -> dict[str, Any]:
    store = store_of(request)
    entry = services.import_manifest(store, body.manifest, allow_unbuilt=False)
    return catalog_view(store, entry)


@router.get("/catalog/agents")
async def list_catalog(
    request: Request, limit: int = Query(50, ge=1, le=200), cursor: str | None = None
) -> dict[str, Any]:
    store = store_of(request)
    # One item per agent (catalog_view reports its newest version).
    agents: dict[str, CatalogEntry] = {}
    for (agent_id, _), entry in sorted(store.catalog.items()):
        agents.setdefault(agent_id, entry)
    return page([catalog_view(store, e) for e in agents.values()], limit, cursor)


@router.post("/agent-installations", status_code=201)
async def create_installation(body: InstallIn, request: Request) -> dict[str, Any]:
    store = store_of(request)
    installation = services.install(
        store, body.agentId, body.version, body.config, body.approvedPermissions, body.modelBindings
    )
    return installation_view(store, installation)


@router.post("/runs", status_code=201)
async def create_run(
    body: RunIn,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    store = store_of(request)
    run = services.create_run(store, body.installationId, "manual", None, idempotency_key)
    return run_view(store, run)


@router.get("/runs")
async def list_runs(
    request: Request, limit: int = Query(50, ge=1, le=200), cursor: str | None = None
) -> dict[str, Any]:
    store = store_of(request)
    runs = sorted(store.runs.values(), key=lambda r: r.created_at, reverse=True)
    return page([run_view(store, r) for r in runs], limit, cursor)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    store = store_of(request)
    return run_view(store, services.get_run(store, run_id))


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request) -> dict[str, Any]:
    store = store_of(request)
    run = services.cancel_run(store, run_id)
    await store.notify()
    return run_view(store, run)


@router.post("/runs/{run_id}/retry")
async def retry_run(run_id: str, request: Request) -> dict[str, Any]:
    store = store_of(request)
    run = services.retry_run(store, run_id)
    await store.notify()
    return run_view(store, run)


@router.get("/runs/{run_id}/events/history")
async def run_events_history(
    run_id: str,
    request: Request,
    after: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
) -> list[dict[str, Any]]:
    store = store_of(request)
    services.get_run(store, run_id)
    return [event_view(e) for e in store.events_after(run_id, after)[:limit]]


@router.get("/runs/{run_id}/events", response_model=None)
async def stream_run_events(
    run_id: str,
    request: Request,
    after: int = Query(0, ge=0),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    store = store_of(request)
    run = services.get_run(store, run_id)
    start = max(after, int(last_event_id) if last_event_id and last_event_id.isdigit() else 0)

    def newer_than(sequence: int) -> Callable[[], bool]:
        return lambda: bool(store.events_after(run_id, sequence))

    async def stream() -> AsyncIterator[str]:
        last = start
        while True:
            for event in store.events_after(run_id, last):
                last = event.sequence
                data = json.dumps(event_view(event))
                yield f"id: {event.sequence}\nevent: {event.type}\ndata: {data}\n\n"
            if run.state in SETTLED:
                return
            if not await store.wait_until(newer_than(last), 15):
                yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/input-requests")
async def list_inputs(
    request: Request,
    state: Literal["pending", "answered", "expired", "cancelled", "all"] = "pending",
    runId: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> dict[str, Any]:
    store = store_of(request)
    for item in list(store.inputs.values()):
        services.expire_if_due(store, item)
    items = sorted(store.inputs.values(), key=lambda r: r.created_at, reverse=True)
    selected = [
        r for r in items if state in ("all", r.state) and (runId is None or r.run_id == runId)
    ]
    return page([input_control_view(store, r) for r in selected], limit, cursor)


@router.post("/input-requests/{input_request_id}/answer")
async def answer_input(input_request_id: str, body: AnswerIn, request: Request) -> dict[str, Any]:
    store = store_of(request)
    answered = services.answer_input(store, input_request_id, body.version, body.value)
    await store.notify()
    return input_control_view(store, answered)
