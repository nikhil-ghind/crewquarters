"""Timezone-aware schedules (PLAN.md sections 7.1 and 13.11)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_api import idempotency, schemas, views
from crewquarters_api.deps import AppState, AuthContext, app_state, current_auth, get_db, request_id
from crewquarters_api.pagination import clamp_limit, decode_cursor, encode_cursor
from crewquarters_shared import audit
from crewquarters_shared.cron import (
    next_occurrence,
    next_occurrences,
    validate_cron,
    validate_timezone,
)
from crewquarters_shared.db.models import (
    AgentCatalogEntry,
    AgentInstallation,
    AgentVersion,
    Schedule,
)
from crewquarters_shared.errors import conflict, not_found
from crewquarters_shared.timeutil import utcnow

router = APIRouter(tags=["schedules"])

ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": schemas.ErrorResponse},
    409: {"model": schemas.ErrorResponse},
    422: {"model": schemas.ErrorResponse},
}


async def _installation(
    db: AsyncSession, installation_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[AgentInstallation, str]:
    row = (
        await db.execute(
            select(AgentInstallation, AgentCatalogEntry.name)
            .join(AgentCatalogEntry, AgentCatalogEntry.agent_id == AgentInstallation.agent_id)
            .where(
                AgentInstallation.id == installation_id,
                AgentInstallation.user_id == user_id,
                AgentInstallation.deleted_at.is_(None),
            )
        )
    ).one_or_none()
    if row is None:
        raise not_found("Installation", installation_id)
    return row[0], row[1]


async def _out(
    db: AsyncSession, state: AppState, schedule: Schedule, name: str
) -> schemas.ScheduleOut:
    installation = await db.get(AgentInstallation, schedule.installation_id)
    assert installation is not None
    version = await db.get(AgentVersion, installation.agent_version_id)
    assert version is not None
    readiness = await views.readiness(installation, version, state.models, state.connections)
    return views.schedule_out(schedule, name, readiness)


async def _schedule(
    db: AsyncSession, schedule_id: uuid.UUID, user_id: uuid.UUID, lock: bool = False
) -> tuple[Schedule, str]:
    stmt = (
        select(Schedule, AgentCatalogEntry.name)
        .join(AgentInstallation, AgentInstallation.id == Schedule.installation_id)
        .join(AgentCatalogEntry, AgentCatalogEntry.agent_id == AgentInstallation.agent_id)
        .where(
            Schedule.id == schedule_id,
            AgentInstallation.user_id == user_id,
            Schedule.deleted_at.is_(None),
        )
    )
    if lock:
        stmt = stmt.with_for_update(of=Schedule)
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        raise not_found("Schedule", schedule_id)
    return row[0], row[1]


@router.post(
    "/schedules/preview",
    response_model=schemas.SchedulePreviewOut,
    responses=ERRORS,
    summary="Preview the next occurrences of a cron expression in a timezone",
)
async def preview_schedule(
    body: schemas.SchedulePreviewIn, _: AuthContext = Depends(current_auth)
) -> schemas.SchedulePreviewOut:
    cron = validate_cron(body.cron)
    validate_timezone(body.timezone)
    times = next_occurrences(cron, body.timezone, utcnow(), body.count)
    return schemas.SchedulePreviewOut(
        cron=cron,
        timezone=body.timezone,
        occurrences=[views.occurrence_out(t, body.timezone) for t in times],
    )


@router.post(
    "/schedules",
    response_model=schemas.ScheduleOut,
    status_code=201,
    responses=ERRORS,
    summary="Create a schedule",
)
async def create_schedule(
    body: schemas.ScheduleCreateIn,
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
    installation, name = await _installation(db, body.installation_id, auth.user.id)
    version = await db.get(AgentVersion, installation.agent_version_id)
    assert version is not None
    if "schedule" not in version.manifest["spec"]["triggers"]:
        raise conflict("TRIGGER_NOT_SUPPORTED", "This agent cannot be scheduled.")
    cron = validate_cron(body.cron)
    validate_timezone(body.timezone)
    schedule = Schedule(
        installation_id=installation.id,
        cron=cron,
        timezone=body.timezone,
        misfire_policy=body.misfire_policy,
        enabled=body.enabled,
        next_run_at=next_occurrence(cron, body.timezone, utcnow()) if body.enabled else None,
        version=1,
    )
    db.add(schedule)
    await db.flush()
    audit.record(
        db,
        action="schedule.created",
        actor_type="user",
        actor_id=auth.user.id,
        target_type="schedule",
        target_id=schedule.id,
        request_id=request_id(request),
        metadata={"cron": cron, "timezone": body.timezone, "misfirePolicy": body.misfire_policy},
    )
    await db.refresh(schedule)
    return await idempotency.finish(db, idem, 201, await _out(db, state, schedule, name))


@router.get(
    "/schedules",
    response_model=schemas.Page[schemas.ScheduleOut],
    summary="List schedules, newest first (sort by nextRunAt client-side for 'upcoming')",
)
async def list_schedules(
    installation_id: uuid.UUID | None = Query(None, alias="installationId"),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.Page[schemas.ScheduleOut]:
    limit = clamp_limit(limit)
    stmt = (
        select(Schedule, AgentCatalogEntry.name)
        .join(AgentInstallation, AgentInstallation.id == Schedule.installation_id)
        .join(AgentCatalogEntry, AgentCatalogEntry.agent_id == AgentInstallation.agent_id)
        .where(AgentInstallation.user_id == auth.user.id, Schedule.deleted_at.is_(None))
        .order_by(Schedule.id.desc())
        .limit(limit + 1)
    )
    if installation_id:
        stmt = stmt.where(Schedule.installation_id == installation_id)
    after = decode_cursor(cursor)
    if after:
        stmt = stmt.where(Schedule.id < after)
    rows = (await db.execute(stmt)).all()
    items = [await _out(db, state, sched, name) for sched, name in rows[:limit]]
    more = len(rows) > limit
    return schemas.Page(
        items=items, next_cursor=encode_cursor(rows[limit - 1][0].id) if more else None
    )


@router.get(
    "/schedules/{schedule_id}",
    response_model=schemas.ScheduleOut,
    responses=ERRORS,
    summary="Get a schedule",
)
async def get_schedule(
    schedule_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.ScheduleOut:
    schedule, name = await _schedule(db, schedule_id, auth.user.id)
    return await _out(db, state, schedule, name)


@router.patch(
    "/schedules/{schedule_id}",
    response_model=schemas.ScheduleOut,
    responses=ERRORS,
    summary="Update a schedule",
)
async def update_schedule(
    schedule_id: uuid.UUID,
    body: schemas.SchedulePatchIn,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    idem = await idempotency.begin(
        db, request, auth.user.id, {"id": str(schedule_id), **body.model_dump(by_alias=True)}
    )
    if idem.replay:
        return idem.replay
    schedule, name = await _schedule(db, schedule_id, auth.user.id, lock=True)
    if schedule.version != body.version:
        raise conflict(
            "VERSION_CONFLICT", "The schedule changed; reload it.", currentVersion=schedule.version
        )
    changes: dict[str, Any] = {}
    if body.cron is not None:
        schedule.cron = validate_cron(body.cron)
        changes["cron"] = schedule.cron
    if body.timezone is not None:
        validate_timezone(body.timezone)
        schedule.timezone = body.timezone
        changes["timezone"] = body.timezone
    if body.misfire_policy is not None:
        schedule.misfire_policy = body.misfire_policy
        changes["misfirePolicy"] = body.misfire_policy
    if body.enabled is not None:
        schedule.enabled = body.enabled
        changes["enabled"] = body.enabled
    if {"cron", "timezone", "enabled"} & changes.keys():
        schedule.next_run_at = (
            next_occurrence(schedule.cron, schedule.timezone, utcnow())
            if schedule.enabled
            else None
        )
    schedule.version += 1
    audit.record(
        db,
        action="schedule.updated",
        actor_type="user",
        actor_id=auth.user.id,
        target_type="schedule",
        target_id=schedule.id,
        request_id=request_id(request),
        metadata=changes,
    )
    await db.flush()
    await db.refresh(schedule)
    return await idempotency.finish(db, idem, 200, await _out(db, state, schedule, name))


@router.delete(
    "/schedules/{schedule_id}", status_code=204, responses=ERRORS, summary="Delete a schedule"
)
async def delete_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    idem = await idempotency.begin(db, request, auth.user.id, {"id": str(schedule_id)})
    if idem.replay:
        return idem.replay
    schedule, _ = await _schedule(db, schedule_id, auth.user.id, lock=True)
    schedule.deleted_at = utcnow()
    schedule.enabled = False
    schedule.next_run_at = None
    audit.record(
        db,
        action="schedule.deleted",
        actor_type="user",
        actor_id=auth.user.id,
        target_type="schedule",
        target_id=schedule.id,
        request_id=request_id(request),
    )
    return await idempotency.finish(db, idem, 204, None)
