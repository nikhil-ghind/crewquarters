"""Internal run API for the capability broker and model gateway (``/internal/v1``).

These routes are never routed by the reverse proxy. Every call requires the local
service credential. The broker validates the agent's capability token first and
then calls these routes on the agent's behalf; each call names the attempt so a
stale attempt can never change a newer one.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_api import chaining, schemas, views
from crewquarters_api.deps import AppState, app_state, get_db, internal_auth
from crewquarters_shared.db.models import AgentRun, AgentVersion, InputRequest
from crewquarters_shared.errors import not_found
from crewquarters_shared.metrics import CONTENT_TYPE
from crewquarters_shared.runs import service
from crewquarters_shared.runs.events import event_validator
from crewquarters_shared.timeutil import utcnow

router = APIRouter(dependencies=[Depends(internal_auth)], tags=["internal"])

ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": schemas.ErrorResponse},
    404: {"model": schemas.ErrorResponse},
    409: {"model": schemas.ErrorResponse},
    422: {"model": schemas.ErrorResponse},
}
LONG_POLL_STEP_SECONDS = 0.5


@router.get(
    "/runs/{run_id}",
    response_model=schemas.InternalRunOut,
    responses=ERRORS,
    summary="Run status for capability checks",
)
async def internal_run(
    run_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> schemas.InternalRunOut:
    run = await db.get(AgentRun, run_id)
    if run is None:
        raise not_found("Run", run_id)
    attempt = await service.get_attempt(db, run.id, run.current_attempt)
    version = await db.get(AgentVersion, run.agent_version_id)
    assert version is not None
    now = utcnow()
    return schemas.InternalRunOut(
        capability_token_id=attempt.capability_token_id if attempt else None,
        id=run.id,
        state=run.state,
        current_attempt=run.current_attempt,
        installation_id=run.installation_id,
        trigger=run.trigger,
        parent_run_id=run.parent_run_id,
        trigger_input=run.trigger_input,
        scheduled_for=run.scheduled_for,
        agent_id=version.agent_id,
        agent_version=version.version,
        agent_version_id=version.id,
        created_at=run.created_at,
        active_timeout_seconds=run.active_timeout_seconds,
        active_seconds_remaining=round(
            max(0.0, run.active_timeout_seconds - service.active_seconds(run, now)), 3
        ),
        max_input_wait_seconds=run.max_input_wait_seconds,
        input_wait_remaining_seconds=round(
            max(0.0, run.max_input_wait_seconds - service.wait_seconds(run, now)), 3
        ),
        cancel_requested=run.cancel_requested_at is not None,
        permissions=run.permissions_snapshot,
        model_bindings=run.model_bindings,
        config=run.config_snapshot,
    )


@router.post(
    "/runs/{run_id}/handshake",
    response_model=schemas.HeartbeatOut,
    responses=ERRORS,
    summary="SDK handshake: PREPARING -> RUNNING",
)
async def handshake(
    run_id: uuid.UUID,
    body: schemas.AttemptIn,
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.HeartbeatOut:
    run = await service.handshake(
        db, run_id, body.attempt, state.settings.heartbeat_timeout_seconds
    )
    await db.commit()
    return schemas.HeartbeatOut(
        state=run.state, cancel_requested=run.cancel_requested_at is not None
    )


@router.post(
    "/runs/{run_id}/heartbeat",
    response_model=schemas.HeartbeatOut,
    responses=ERRORS,
    summary="Extend the attempt heartbeat lease",
)
async def heartbeat(
    run_id: uuid.UUID,
    body: schemas.AttemptIn,
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.HeartbeatOut:
    result = await service.heartbeat(
        db, run_id, body.attempt, state.settings.heartbeat_timeout_seconds
    )
    await db.commit()
    return schemas.HeartbeatOut(state=result["state"], cancel_requested=result["cancelRequested"])


@router.post(
    "/runs/{run_id}/events",
    response_model=schemas.RunEventOut,
    status_code=201,
    responses=ERRORS,
    summary="Append a log/progress/metric/artifact event",
)
async def agent_event(
    run_id: uuid.UUID, body: schemas.AgentEventIn, db: AsyncSession = Depends(get_db)
) -> schemas.RunEventOut:
    event = await service.record_agent_event(db, run_id, body.attempt, body.type, body.payload)
    await db.commit()
    return schemas.RunEventOut.model_validate(event)


@router.post(
    "/runs/{run_id}/event-batches",
    response_model=schemas.EventBatchOut,
    responses=ERRORS,
    summary="Append a batch of agent events atomically, deduplicated by clientEventId",
)
async def agent_event_batch(
    run_id: uuid.UUID,
    body: schemas.EventBatchIn,
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.EventBatchOut:
    """Every event is validated first (type, size, and the run-event schema); if any is
    invalid nothing is stored and ``422 INVALID_EVENT`` lists each rejected event as
    ``details.rejected[] = {index, clientEventId, code, errors}``. Otherwise all new events
    are inserted in one transaction, and a ``clientEventId`` already stored for the run is
    skipped, so a retried batch never duplicates events."""
    result = await service.record_agent_event_batch(
        db,
        run_id,
        body.attempt,
        [
            service.AgentEventInput(
                client_event_id=e.client_event_id,
                type=e.type,
                payload=e.payload,
                occurred_at=e.occurred_at,
            )
            for e in body.events
        ],
        validator=event_validator(state.settings.contracts_dir),
    )
    await db.commit()
    return schemas.EventBatchOut(
        accepted=result.accepted, duplicates=result.duplicates, last_sequence=result.last_sequence
    )


@router.post(
    "/runs/{run_id}/model-state",
    response_model=schemas.HeartbeatOut,
    responses=ERRORS,
    summary="Model gateway: RUNNING <-> LOADING_MODEL",
)
async def model_state(
    run_id: uuid.UUID, body: schemas.ModelStateIn, db: AsyncSession = Depends(get_db)
) -> schemas.HeartbeatOut:
    run = await service.set_model_loading(db, run_id, body.attempt, body.loading, body.model)
    await db.commit()
    return schemas.HeartbeatOut(
        state=run.state, cancel_requested=run.cancel_requested_at is not None
    )


@router.post(
    "/runs/{run_id}/result",
    response_model=schemas.HeartbeatOut,
    responses=ERRORS,
    summary="Post the final result (first result wins)",
)
async def run_result(
    run_id: uuid.UUID, body: schemas.RunResultIn, db: AsyncSession = Depends(get_db)
) -> schemas.HeartbeatOut:
    run = await service.finish(
        db, run_id, body.attempt, status=body.status, result=body.result, error=body.error
    )
    await db.commit()
    return schemas.HeartbeatOut(
        state=run.state, cancel_requested=run.cancel_requested_at is not None
    )


@router.post(
    "/runs/{run_id}/input-requests",
    response_model=schemas.InputRequestOut,
    responses=ERRORS,
    summary="ctx.input.ask: create or return the request for a stable key",
)
async def ask(
    run_id: uuid.UUID, body: schemas.AskIn, db: AsyncSession = Depends(get_db)
) -> schemas.InputRequestOut:
    request = await service.ask(
        db,
        run_id,
        body.attempt,
        key=body.key,
        title=body.title,
        prompt=body.prompt,
        schema=body.schema_,
        timeout_seconds=body.timeout_seconds,
        preview=body.preview,
    )
    await db.commit()
    return views.input_out(request, None)


@router.get(
    "/input-requests/{input_request_id}",
    response_model=schemas.InputRequestOut,
    responses=ERRORS,
    summary="Long-poll an input request until it is closed or the wait elapses",
)
async def poll_input(
    input_request_id: uuid.UUID,
    wait: float = Query(0, ge=0, le=30, description="Seconds to wait for the request to close."),
    state: AppState = Depends(app_state),
) -> schemas.InputRequestOut:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait
    while True:
        async with state.sessions() as db:
            request = await db.scalar(
                select(InputRequest).where(InputRequest.id == input_request_id)
            )
        if request is None:
            raise not_found("Input request", input_request_id)
        if request.state != "pending" or loop.time() >= deadline:
            return views.input_out(request, None)
        await asyncio.sleep(min(LONG_POLL_STEP_SECONDS, max(0.0, deadline - loop.time())))


@router.post(
    "/runs/{run_id}/agent-runs",
    response_model=schemas.AgentStartOut,
    responses=ERRORS,
    summary="ctx.agents.start: start another agent from this run (idempotent per startKey)",
)
async def start_agent(
    run_id: uuid.UUID,
    body: schemas.AgentStartIn,
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.AgentStartOut:
    child, created = await chaining.start_agent_run(
        db,
        state,
        run_id,
        body.attempt,
        agent_id=body.agent_id,
        start_key=body.start_key,
        trigger_input=body.input,
    )
    await db.commit()
    return schemas.AgentStartOut(
        run_id=child.id,
        agent_id=body.agent_id,
        installation_id=child.installation_id,
        state=child.state,
        created=created,
    )


@router.post(
    "/runs/{run_id}/actions/{key}/claim",
    response_model=schemas.ActionOut,
    responses=ERRORS,
    summary="ctx.idempotency: claim an external action key",
)
async def claim_action(
    run_id: uuid.UUID, key: str, body: schemas.ActionClaimIn, db: AsyncSession = Depends(get_db)
) -> schemas.ActionOut:
    """A repeat with the same ``claimToken`` from the same attempt (a retry after a lost
    response) returns the original ``claimed``; any other repeat of an uncompleted key is
    ``in_doubt``."""
    result = await service.claim_action(db, run_id, body.attempt, key, body.claim_token)
    await db.commit()
    return schemas.ActionOut.model_validate(result)


@router.post(
    "/runs/{run_id}/actions/{key}/complete",
    response_model=schemas.ActionOut,
    responses=ERRORS,
    summary="ctx.idempotency: record an action's result",
)
async def complete_action(
    run_id: uuid.UUID, key: str, body: schemas.ActionCompleteIn, db: AsyncSession = Depends(get_db)
) -> schemas.ActionOut:
    result = await service.complete_action(db, run_id, body.attempt, key, body.result)
    await db.commit()
    return schemas.ActionOut.model_validate(result)


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    responses={200: {"content": {"text/plain": {}}}},
    summary="Prometheus metrics: HTTP latency/errors, job depth/age, runs, pending inputs",
)
async def metrics(state: AppState = Depends(app_state)) -> PlainTextResponse:
    async with state.sessions() as db:
        await state.metrics.collect_database(db)
    return PlainTextResponse(state.metrics.render(), media_type=CONTENT_TYPE)
