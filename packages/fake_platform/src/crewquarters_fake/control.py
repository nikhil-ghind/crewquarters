"""Control-API slice (/api/v1) from packages/contracts/openapi.yaml."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from crewquarters_fake import services
from crewquarters_fake.statemachine import SETTLED
from crewquarters_fake.store import Store
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
    version: str
    config: dict[str, Any] = {}
    approvedPermissions: dict[str, Any] | None = None


class RunIn(BaseModel):
    installationId: str
    trigger: Literal["manual", "schedule"] = "manual"
    scheduledFor: str | None = None


class AnswerIn(BaseModel):
    version: int
    data: Any = None


@router.post("/catalog/agents:import")
async def import_agent(body: ImportIn, request: Request) -> dict[str, Any]:
    return catalog_view(services.import_manifest(store_of(request), body.manifest, allow_unbuilt=False))


@router.get("/catalog/agents")
async def list_catalog(
    request: Request, limit: int = Query(50, ge=1, le=200), cursor: str | None = None
) -> dict[str, Any]:
    items = [catalog_view(e) for e in store_of(request).catalog.values()]
    return page(items, limit, cursor)


@router.post("/agent-installations")
async def create_installation(body: InstallIn, request: Request) -> dict[str, Any]:
    installation = services.install(
        store_of(request), body.agentId, body.version, body.config, body.approvedPermissions
    )
    return installation_view(installation)


@router.post("/runs")
async def create_run(
    body: RunIn, request: Request, idempotency_key: str | None = Header(None, alias="Idempotency-Key")
) -> dict[str, Any]:
    run = services.create_run(
        store_of(request), body.installationId, body.trigger, body.scheduledFor, idempotency_key
    )
    return run_view(run)


@router.get("/runs")
async def list_runs(
    request: Request, limit: int = Query(50, ge=1, le=200), cursor: str | None = None
) -> dict[str, Any]:
    runs = sorted(store_of(request).runs.values(), key=lambda r: r.created_at, reverse=True)
    return page([run_view(r) for r in runs], limit, cursor)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    return run_view(services.get_run(store_of(request), run_id))


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request) -> dict[str, Any]:
    store = store_of(request)
    run = services.cancel_run(store, run_id)
    await store.notify()
    return run_view(run)


@router.post("/runs/{run_id}/retry")
async def retry_run(run_id: str, request: Request) -> dict[str, Any]:
    store = store_of(request)
    run = services.retry_run(store, run_id)
    await store.notify()
    return run_view(run)


@router.get("/runs/{run_id}/events", response_model=None)
async def run_events(
    run_id: str,
    request: Request,
    after: int = Query(0, ge=0),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> dict[str, Any] | StreamingResponse:
    store = store_of(request)
    run = services.get_run(store, run_id)
    if "text/event-stream" not in request.headers.get("accept", ""):
        return {"items": [event_view(e) for e in store.events_after(run_id, after)]}
    start = max(after, int(last_event_id) if last_event_id and last_event_id.isdigit() else 0)

    def newer_than(sequence: int) -> Callable[[], bool]:
        return lambda: bool(store.events_after(run_id, sequence))

    async def stream() -> AsyncIterator[str]:
        last = start
        while True:
            for event in store.events_after(run_id, last):
                last = event.sequence
                yield f"id: {event.sequence}\nevent: {event.type}\ndata: {json.dumps(event_view(event))}\n\n"
            if run.state in SETTLED:
                return
            changed = await store.wait_until(newer_than(last), 15)
            if not changed:
                yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/input-requests")
async def list_inputs(
    request: Request,
    state: Literal["pending", "answered", "expired", "cancelled"] | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> dict[str, Any]:
    store = store_of(request)
    for item in list(store.inputs.values()):
        services.expire_if_due(store, item)
    items = sorted(store.inputs.values(), key=lambda r: r.created_at, reverse=True)
    views = [input_control_view(r) for r in items if state is None or r.state == state]
    return page(views, limit, cursor)


@router.post("/input-requests/{request_id}/answer")
async def answer_input(request_id: str, body: AnswerIn, request: Request) -> dict[str, Any]:
    store = store_of(request)
    answered = services.answer_input(store, request_id, body.version, body.data, "owner")
    await store.notify()
    return input_control_view(answered)
