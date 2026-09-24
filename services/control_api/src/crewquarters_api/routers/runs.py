"""Runs, run event streaming (SSE), and human input requests."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_api import idempotency, schemas, views
from crewquarters_api.deps import AppState, AuthContext, app_state, current_auth, get_db, request_id
from crewquarters_api.pagination import clamp_limit, decode_cursor, encode_cursor
from crewquarters_shared import audit
from crewquarters_shared.db.models import (
    AgentCatalogEntry,
    AgentInstallation,
    AgentRun,
    AgentVersion,
    InputRequest,
    RunEvent,
    Schedule,
)
from crewquarters_shared.errors import conflict, invalid, not_found
from crewquarters_shared.runs import service
from crewquarters_shared.runs.states import TERMINAL_STATES, RunState
from crewquarters_shared.timeutil import utcnow

router = APIRouter(tags=["runs"])

ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": schemas.ErrorResponse},
    409: {"model": schemas.ErrorResponse},
    422: {"model": schemas.ErrorResponse},
}
SSE_POLL_SECONDS = 0.5
SSE_KEEPALIVE_SECONDS = 15.0


async def _owned_run(db: AsyncSession, run_id: uuid.UUID, user_id: uuid.UUID) -> AgentRun:
    run = await db.scalar(
        select(AgentRun)
        .join(AgentInstallation, AgentInstallation.id == AgentRun.installation_id)
        .where(AgentRun.id == run_id, AgentInstallation.user_id == user_id)
    )
    if run is None:
        raise not_found("Run", run_id)
    return run


@router.post(
    "/runs",
    response_model=schemas.RunOut,
    status_code=201,
    responses=ERRORS,
    summary="Start a manual run",
)
async def create_run(
    body: schemas.RunCreateIn,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    idem = await idempotency.begin(
        db, request, auth.user.id, body.model_dump(by_alias=True, mode="json")
    )
    if idem.replay:
        return idem.replay
    installation = await db.scalar(
        select(AgentInstallation).where(
            AgentInstallation.id == body.installation_id,
            AgentInstallation.user_id == auth.user.id,
            AgentInstallation.deleted_at.is_(None),
        )
    )
    if installation is None:
        raise not_found("Installation", body.installation_id)
    version = await db.get(AgentVersion, installation.agent_version_id)
    assert version is not None
    if "manual" not in version.manifest["spec"]["triggers"]:
        raise conflict("TRIGGER_NOT_SUPPORTED", "This agent cannot be run manually.")
    readiness = await views.readiness(installation, version, state.models, state.connections)
    if not readiness.ready:
        raise conflict(
            "INSTALLATION_NOT_READY",
            "This agent is not ready to run.",
            checks=[c.model_dump(by_alias=True) for c in readiness.checks if c.status != "ok"],
        )
    created = await service.create_run(
        db, installation, version, trigger="manual", created_by=auth.user.id
    )
    audit.record(
        db,
        action="run.created",
        actor_type="user",
        actor_id=auth.user.id,
        target_type="run",
        target_id=created.run.id,
        request_id=request_id(request),
        metadata={"installationId": str(installation.id), "trigger": "manual"},
    )
    await db.flush()
    await db.refresh(created.run)
    return await idempotency.finish(db, idem, 201, await views.run_out(db, created.run))


@router.get("/runs", response_model=schemas.Page[schemas.RunOut], summary="List runs, newest first")
async def list_runs(
    installation_id: uuid.UUID | None = Query(None, alias="installationId"),
    state_filter: list[schemas.RunStateLiteral] | None = Query(None, alias="state"),
    trigger: str | None = Query(None, pattern="^(manual|schedule)$"),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
) -> schemas.Page[schemas.RunOut]:
    limit = clamp_limit(limit)
    stmt = (
        select(AgentRun)
        .join(AgentInstallation, AgentInstallation.id == AgentRun.installation_id)
        .where(AgentInstallation.user_id == auth.user.id)
        .order_by(AgentRun.id.desc())
        .limit(limit + 1)
    )
    if installation_id:
        stmt = stmt.where(AgentRun.installation_id == installation_id)
    if state_filter:
        stmt = stmt.where(AgentRun.state.in_(state_filter))
    if trigger:
        stmt = stmt.where(AgentRun.trigger == trigger)
    after = decode_cursor(cursor)
    if after:
        stmt = stmt.where(AgentRun.id < after)
    rows = list((await db.scalars(stmt)).all())
    items = await views.runs_out(db, rows[:limit])
    return schemas.Page(
        items=items, next_cursor=encode_cursor(rows[limit - 1].id) if len(rows) > limit else None
    )


@router.get("/runs/{run_id}", response_model=schemas.RunOut, responses=ERRORS, summary="Get a run")
async def get_run(
    run_id: uuid.UUID, auth: AuthContext = Depends(current_auth), db: AsyncSession = Depends(get_db)
) -> schemas.RunOut:
    return await views.run_out(db, await _owned_run(db, run_id, auth.user.id))


async def _require_ready(db: AsyncSession, state: AppState, installation_id: uuid.UUID) -> None:
    installation = await db.get(AgentInstallation, installation_id)
    if installation is None or installation.deleted_at is not None:
        raise conflict("INSTALLATION_NOT_READY", "This agent has been uninstalled.")
    version = await db.get(AgentVersion, installation.agent_version_id)
    assert version is not None
    readiness = await views.readiness(installation, version, state.models, state.connections)
    if not readiness.ready:
        raise conflict(
            "INSTALLATION_NOT_READY",
            "This agent is not ready to run.",
            checks=[c.model_dump(by_alias=True) for c in readiness.checks if c.status != "ok"],
        )


async def _run_action(
    run_id: uuid.UUID,
    request: Request,
    auth: AuthContext,
    state: AppState,
    db: AsyncSession,
    action: str,
) -> Response:
    idem = await idempotency.begin(
        db, request, auth.user.id, {"runId": str(run_id), "action": action}
    )
    if idem.replay:
        return idem.replay
    owned = await _owned_run(db, run_id, auth.user.id)
    if action == "retry":
        await _require_ready(db, state, owned.installation_id)
    run = await service.lock_run(db, run_id)
    if action == "cancel":
        await service.request_cancel(db, run, reason="owner_cancel")
    else:
        await service.retry(db, run)
    audit.record(
        db,
        action=f"run.{action}",
        actor_type="user",
        actor_id=auth.user.id,
        target_type="run",
        target_id=run.id,
        request_id=request_id(request),
    )
    await db.flush()
    await db.refresh(run)
    return await idempotency.finish(db, idem, 200, await views.run_out(db, run))


@router.post(
    "/runs/{run_id}/cancel", response_model=schemas.RunOut, responses=ERRORS, summary="Cancel a run"
)
async def cancel_run(
    run_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await _run_action(run_id, request, auth, state, db, "cancel")


@router.post(
    "/runs/{run_id}/retry",
    response_model=schemas.RunOut,
    responses=ERRORS,
    summary="Retry an interrupted or retryable failed run as a new attempt",
)
async def retry_run(
    run_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await _run_action(run_id, request, auth, state, db, "retry")


@router.get(
    "/runs/{run_id}/events/history",
    response_model=list[schemas.RunEventOut],
    responses=ERRORS,
    summary="Run events as JSON (polling fallback for SSE)",
)
async def run_events_history(
    run_id: uuid.UUID,
    after: int = Query(0, ge=0, description="Return events with sequence greater than this."),
    limit: int = Query(200, ge=1, le=500),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.RunEventOut]:
    await _owned_run(db, run_id, auth.user.id)
    events = (
        await db.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.sequence > after)
            .order_by(RunEvent.sequence)
            .limit(limit)
        )
    ).all()
    return [schemas.RunEventOut.model_validate(e) for e in events]


@router.get(
    "/runs/{run_id}/events",
    responses={
        200: {
            "description": "Server-sent events. Each event has id=sequence, event=<type>, "
            "data=RunEvent JSON. The stream ends with an 'end' event when the run reaches "
            "a terminal state. Reconnect with Last-Event-ID to resume without duplicates.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        **ERRORS,
    },
    summary="Stream run events (SSE)",
)
async def stream_run_events(
    run_id: uuid.UUID,
    request: Request,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    after: int | None = Query(
        None, ge=0, description="Resume after this sequence (alternative to Last-Event-ID)."
    ),
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    await _owned_run(db, run_id, auth.user.id)
    try:
        cursor = int(last_event_id) if last_event_id else (after or 0)
    except ValueError as exc:
        raise invalid(
            "INVALID_LAST_EVENT_ID", "Last-Event-ID must be an event sequence number."
        ) from exc
    # The stream polls with its own short sessions; release this request's
    # connection now instead of holding it for the life of the stream.
    await db.close()
    return StreamingResponse(
        _event_stream(state, request, run_id, cursor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def _event_stream(
    state: AppState, request: Request, run_id: uuid.UUID, cursor: int
) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    last_sent = loop.time()
    yield "retry: 2000\n\n"
    while True:
        if await request.is_disconnected():
            return
        # Read the run state first: if it was already terminal, the events query that
        # follows sees every event committed with that state, so "end" is never early.
        async with state.sessions() as db:
            run_state = await db.scalar(select(AgentRun.state).where(AgentRun.id == run_id))
        async with state.sessions() as db:
            events = (
                await db.scalars(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id, RunEvent.sequence > cursor)
                    .order_by(RunEvent.sequence)
                    .limit(200)
                )
            ).all()
        for event in events:
            data = jsonable_encoder(schemas.RunEventOut.model_validate(event), by_alias=True)
            yield f"id: {event.sequence}\nevent: {event.type}\ndata: {json.dumps(data)}\n\n"
            cursor = event.sequence
            last_sent = loop.time()
        if not events and run_state is not None and RunState(run_state) in TERMINAL_STATES:
            yield f"event: end\ndata: {json.dumps({'state': run_state})}\n\n"
            return
        if loop.time() - last_sent > SSE_KEEPALIVE_SECONDS:
            yield ": keepalive\n\n"
            last_sent = loop.time()
        if not events:
            await asyncio.sleep(SSE_POLL_SECONDS)


# --- Input requests -----------------------------------------------------------------


@router.get(
    "/input-requests",
    response_model=schemas.Page[schemas.InputRequestOut],
    tags=["inputs"],
    summary="List Crew Requests (pending by default)",
)
async def list_input_requests(
    state_filter: str = Query(
        "pending", alias="state", pattern="^(pending|answered|cancelled|expired|all)$"
    ),
    run_id: uuid.UUID | None = Query(None, alias="runId"),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
) -> schemas.Page[schemas.InputRequestOut]:
    limit = clamp_limit(limit)
    stmt = (
        select(InputRequest, AgentCatalogEntry.name)
        .join(AgentRun, AgentRun.id == InputRequest.run_id)
        .join(AgentInstallation, AgentInstallation.id == AgentRun.installation_id)
        .join(AgentCatalogEntry, AgentCatalogEntry.agent_id == AgentInstallation.agent_id)
        .where(AgentInstallation.user_id == auth.user.id)
        .order_by(InputRequest.id.desc())
        .limit(limit + 1)
    )
    after = decode_cursor(cursor)
    if after:
        stmt = stmt.where(InputRequest.id < after)
    if state_filter != "all":
        stmt = stmt.where(InputRequest.state == state_filter)
    if run_id:
        stmt = stmt.where(InputRequest.run_id == run_id)
    rows = (await db.execute(stmt)).all()
    more = len(rows) > limit
    return schemas.Page(
        items=[views.input_out(req, name) for req, name in rows[:limit]],
        next_cursor=encode_cursor(rows[limit - 1][0].id) if more else None,
    )


# --- Attention (Activity badge and dashboard "Needs attention") -----------------------


@router.post(
    "/runs/{run_id}/acknowledge",
    response_model=schemas.RunOut,
    responses=ERRORS,
    summary="Acknowledge a failed or interrupted run so it leaves the attention list",
)
async def acknowledge_run(
    run_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    idem = await idempotency.begin(db, request, auth.user.id, {"runId": str(run_id)})
    if idem.replay:
        return idem.replay
    await _owned_run(db, run_id, auth.user.id)
    run = await service.lock_run(db, run_id)
    if RunState(run.state) not in TERMINAL_STATES:
        raise conflict("RUN_NOT_FINISHED", "Only finished runs can be acknowledged.")
    if run.acknowledged_at is None:
        run.acknowledged_at = utcnow()
    await db.flush()
    await db.refresh(run)
    return await idempotency.finish(db, idem, 200, await views.run_out(db, run))


@router.get(
    "/attention",
    response_model=schemas.AttentionOut,
    tags=["inputs"],
    summary="Items that need the owner: questions, unacknowledged failures, blocked "
    "schedules, and models needing action",
)
async def attention(
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.AttentionOut:
    items: list[schemas.AttentionItem] = []
    owned = AgentInstallation.user_id == auth.user.id
    for req, name in (
        await db.execute(
            select(InputRequest, AgentCatalogEntry.name)
            .join(AgentRun, AgentRun.id == InputRequest.run_id)
            .join(AgentInstallation, AgentInstallation.id == AgentRun.installation_id)
            .join(AgentCatalogEntry, AgentCatalogEntry.agent_id == AgentInstallation.agent_id)
            .where(owned, InputRequest.state == "pending")
            .order_by(InputRequest.id)
            .limit(100)
        )
    ).all():
        items.append(
            schemas.AttentionItem(
                kind="input_request",
                title=req.title,
                detail=f"{name} needs your input.",
                run_id=req.run_id,
                input_request_id=req.id,
                created_at=req.created_at,
            )
        )
    for run, name in (
        await db.execute(
            select(AgentRun, AgentCatalogEntry.name)
            .join(AgentInstallation, AgentInstallation.id == AgentRun.installation_id)
            .join(AgentCatalogEntry, AgentCatalogEntry.agent_id == AgentInstallation.agent_id)
            .where(
                owned,
                AgentRun.state.in_([RunState.FAILED.value, RunState.INTERRUPTED.value]),
                AgentRun.acknowledged_at.is_(None),
            )
            .order_by(AgentRun.id.desc())
            .limit(100)
        )
    ).all():
        code = (run.error or {}).get("code", run.state)
        items.append(
            schemas.AttentionItem(
                kind="failed_run",
                title=f"{name} {run.state.lower()}",
                detail=str(code),
                run_id=run.id,
                created_at=run.finished_at,
            )
        )
    for schedule, installation, version, name in (
        await db.execute(
            select(Schedule, AgentInstallation, AgentVersion, AgentCatalogEntry.name)
            .join(AgentInstallation, AgentInstallation.id == Schedule.installation_id)
            .join(AgentVersion, AgentVersion.id == AgentInstallation.agent_version_id)
            .join(AgentCatalogEntry, AgentCatalogEntry.agent_id == AgentInstallation.agent_id)
            .where(owned, Schedule.enabled.is_(True), Schedule.deleted_at.is_(None))
        )
    ).all():
        readiness = await views.readiness(installation, version, state.models, state.connections)
        blockers = [c.detail for c in readiness.checks if c.status != "ok"]
        if blockers:
            items.append(
                schemas.AttentionItem(
                    kind="schedule_blocked",
                    title=f"{name} schedule is blocked",
                    detail=blockers[0],
                    schedule_id=schedule.id,
                )
            )
    for model in await state.models.list_models():
        if model.get("downloadState") == "DOWNLOAD_ERROR" or model.get("memoryState") in (
            "LOAD_ERROR",
            "ERROR",
            "RUNTIME_ERROR",
        ):
            items.append(
                schemas.AttentionItem(
                    kind="model_action",
                    title=f"{model.get('displayName', model['id'])} needs attention",
                    detail=str(model.get("downloadState") or model.get("memoryState")),
                    model_id=model["id"],
                )
            )
    return schemas.AttentionOut(count=len(items), items=items)


@router.post(
    "/input-requests/{input_request_id}/answer",
    response_model=schemas.InputRequestOut,
    tags=["inputs"],
    responses=ERRORS,
    summary="Answer a Crew Request (rejects stale versions and double answers)",
)
async def answer_input_request(
    input_request_id: uuid.UUID,
    body: schemas.InputAnswerIn,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    idem = await idempotency.begin(
        db, request, auth.user.id, {"id": str(input_request_id), **body.model_dump(by_alias=True)}
    )
    if idem.replay:
        return idem.replay
    run_id = await db.scalar(select(InputRequest.run_id).where(InputRequest.id == input_request_id))
    if run_id is None:
        raise not_found("Input request", input_request_id)
    await _owned_run(db, run_id, auth.user.id)
    answered = await service.answer(
        db, input_request_id, version=body.version, value=body.value, user_id=auth.user.id
    )
    audit.record(
        db,
        action="input.answered",
        actor_type="user",
        actor_id=auth.user.id,
        target_type="input_request",
        target_id=answered.id,
        request_id=request_id(request),
        metadata={"runId": str(run_id), "key": answered.key},
    )
    await db.flush()
    return await idempotency.finish(db, idem, 200, views.input_out(answered, None))
