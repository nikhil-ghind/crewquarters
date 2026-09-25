"""Models and connections (read through their owning services), settings, health,
system status, and audit history."""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_api import idempotency, schemas
from crewquarters_api.catalog import host_architecture
from crewquarters_api.deps import (
    AppState,
    AuthContext,
    app_state,
    current_auth,
    get_db,
    request_id,
    require_owner,
)
from crewquarters_api.pagination import (
    clamp_limit,
    decode_cursor,
    encode_cursor,
    page_in_memory,
)
from crewquarters_shared import audit
from crewquarters_shared.cron import validate_timezone
from crewquarters_shared.db.models import AuditEvent, Setting
from crewquarters_shared.errors import conflict, invalid, not_found
from crewquarters_shared.timeutil import utcnow

router = APIRouter()

ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": schemas.ErrorResponse},
    409: {"model": schemas.ErrorResponse},
    422: {"model": schemas.ErrorResponse},
}
API_VERSION = "0.1.0"


# --- Models ---------------------------------------------------------------------------


@router.get(
    "/models",
    response_model=schemas.Page[schemas.ModelOut],
    tags=["models"],
    summary="List model profiles (ordered by id)",
)
async def list_models(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    _: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
) -> schemas.Page[schemas.ModelOut]:
    page, nxt = page_in_memory(await state.models.list_models(), lambda m: m["id"], limit, cursor)
    return schemas.Page(items=[schemas.ModelOut.model_validate(m) for m in page], next_cursor=nxt)


@router.get(
    "/models/{model_id}",
    response_model=schemas.ModelOut,
    tags=["models"],
    responses=ERRORS,
    summary="Get a model profile",
)
async def get_model(
    model_id: str, _: AuthContext = Depends(current_auth), state: AppState = Depends(app_state)
) -> schemas.ModelOut:
    model = await state.models.get_model(model_id)
    if model is None:
        raise not_found("Model", model_id)
    return schemas.ModelOut.model_validate(model)


async def _model_action(
    model_id: str,
    action: str,
    request: Request,
    auth: AuthContext,
    state: AppState,
    db: AsyncSession,
    *,
    force: bool = False,
    status_code: int = 202,
) -> Response:
    idem = await idempotency.begin(
        db, request, auth.user.id, {"modelId": model_id, "action": action, "force": force}
    )
    if idem.replay:
        return idem.replay
    model = await state.models.request_action(
        model_id, action, force=force, actor=str(auth.user.id)
    )
    audit.record(
        db,
        action=f"model.{action}",
        actor_type="user",
        actor_id=auth.user.id,
        target_type="model",
        target_id=model_id,
        request_id=request_id(request),
        metadata={"force": force} if force else None,
    )
    return await idempotency.finish(db, idem, status_code, schemas.ModelOut.model_validate(model))


def _model_route(path: str, action: str, summary: str, status_code: int = 202) -> None:
    @router.post(
        path,
        response_model=schemas.ModelOut,
        status_code=status_code,
        tags=["models"],
        responses=ERRORS,
        summary=summary,
        name=f"model_{action}",
    )
    async def handler(
        model_id: str,
        request: Request,
        auth: AuthContext = Depends(require_owner),
        state: AppState = Depends(app_state),
        db: AsyncSession = Depends(get_db),
    ) -> Response:
        return await _model_action(
            model_id, action, request, auth, state, db, status_code=status_code
        )


_model_route("/models/{model_id}/install", "install", "Download a pinned model to disk (resumable)")
_model_route(
    "/models/{model_id}/load", "load", "Load a model into memory (admission control applies)"
)


@router.post(
    "/models/{model_id}/install/cancel",
    response_model=schemas.ModelOut,
    tags=["models"],
    responses=ERRORS,
    summary="Cancel a download; optionally delete the partial files",
)
async def cancel_model_install(
    model_id: str,
    body: schemas.ModelCancelInstallIn,
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await _model_action(
        model_id, "cancel_install", request, auth, state, db, force=body.clear, status_code=200
    )


@router.post(
    "/models/{model_id}/unload",
    response_model=schemas.ModelOut,
    status_code=202,
    tags=["models"],
    responses=ERRORS,
    summary="Unload a model; refuses while leases are held unless force is set",
)
async def unload_model(
    model_id: str,
    request: Request,
    body: schemas.ModelUnloadIn | None = None,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    force = bool(body and body.force)
    return await _model_action(model_id, "unload", request, auth, state, db, force=force)


@router.delete(
    "/models/{model_id}",
    response_model=schemas.ModelOut,
    tags=["models"],
    responses=ERRORS,
    summary="Delete an installed model's files (it must not be loaded)",
)
async def delete_model(
    model_id: str,
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await _model_action(model_id, "delete", request, auth, state, db, status_code=200)


@router.get(
    "/models/{model_id}/events",
    tags=["models"],
    responses={
        200: {
            "description": "Server-sent events: event=model.state, data=Model JSON, sent "
            "whenever download or load progress changes.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        **ERRORS,
    },
    summary="Stream model download/load progress (SSE)",
)
async def model_events(
    model_id: str,
    _: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    if await state.models.get_model(model_id) is None:
        raise not_found("Model", model_id)
    await db.close()
    events = getattr(state.models, "model_events", None)

    async def relay() -> AsyncIterator[str]:
        if events is None:  # fake client: one snapshot
            model = await state.models.get_model(model_id)
            yield f"id: 1\nevent: model.state\ndata: {json.dumps(model)}\n\n"
            return
        async for line in events(model_id):
            yield line + "\n"

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/system/memory",
    response_model=schemas.MemoryOut,
    tags=["system"],
    summary="Unified memory: system reserve, model reservations, available",
)
async def system_memory(
    _: AuthContext = Depends(current_auth), state: AppState = Depends(app_state)
) -> schemas.MemoryOut:
    return schemas.MemoryOut.model_validate(await state.models.memory())


# --- Connections ------------------------------------------------------------------------


@router.get(
    "/connections",
    response_model=schemas.Page[schemas.ConnectionOut],
    tags=["connections"],
    summary="Connection status, ordered by provider (secrets are never returned)",
)
async def list_connections(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    _: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
) -> schemas.Page[schemas.ConnectionOut]:
    page, nxt = page_in_memory(
        await state.connections.list_connections(), lambda c: c["provider"], limit, cursor
    )
    return schemas.Page(
        items=[schemas.ConnectionOut.model_validate(c) for c in page], next_cursor=nxt
    )


# --- Settings ---------------------------------------------------------------------------

SETTING_DEFAULTS: dict[str, tuple[str, Any]] = {
    "timezone": ("string", "UTC"),
    "idleUnloadSeconds": ("integer", 600),
    "setupCompleted": ("boolean", False),
    "setupState": ("object", {}),
}
# The broker builds the OAuth redirect and Twilio callback URLs from CQ_PUBLIC_BASE_URL at
# startup, and Twilio signatures cover the exact URL, so the callback base is read-only
# here: one source of truth (docs/adr/0009-callback-base-url.md).
TWILIO_CALLBACK_PATH = "/api/v1/callbacks/twilio"
GOOGLE_CALLBACK_PATH = "/api/v1/connections/google/callback"


def callback_urls(public_base_url: str) -> dict[str, str]:
    base = public_base_url.rstrip("/")
    return {
        "googleRedirectUri": base + GOOGLE_CALLBACK_PATH,
        "twilioCallbackBase": base + TWILIO_CALLBACK_PATH,
    }


async def _settings_out(db: AsyncSession, public_base_url: str) -> schemas.SettingsOut:
    rows = {
        s.key: s
        for s in (await db.scalars(select(Setting).where(Setting.key.in_(SETTING_DEFAULTS)))).all()
    }
    values = {k: (rows[k].value if k in rows else d) for k, (_, d) in SETTING_DEFAULTS.items()}
    versions = {k: (rows[k].version if k in rows else 0) for k in SETTING_DEFAULTS}
    return schemas.SettingsOut(
        timezone=values["timezone"],
        idle_unload_seconds=values["idleUnloadSeconds"],
        callback_base_url=public_base_url.rstrip("/"),
        callback_urls=callback_urls(public_base_url),
        setup_completed=values["setupCompleted"],
        setup_state=values["setupState"],
        versions=versions,
    )


@router.get(
    "/settings", response_model=schemas.SettingsOut, tags=["settings"], summary="Platform settings"
)
async def get_settings_route(
    _: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.SettingsOut:
    return await _settings_out(db, state.settings.public_base_url)


@router.patch(
    "/settings",
    response_model=schemas.SettingsOut,
    tags=["settings"],
    responses=ERRORS,
    summary="Update platform settings",
)
async def patch_settings(
    body: schemas.SettingsPatchIn,
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if "callback_base_url" in body.model_fields_set:
        raise invalid(
            "SETTING_READ_ONLY",
            "The callback base URL is set by CQ_PUBLIC_BASE_URL on the device.",
            key="callbackBaseUrl",
        )
    idem = await idempotency.begin(db, request, auth.user.id, body.model_dump(by_alias=True))
    if idem.replay:
        return idem.replay
    updates = body.model_dump(
        by_alias=True, exclude_unset=True, exclude={"versions", "callback_base_url"}
    )
    if "timezone" in updates and updates["timezone"] is not None:
        validate_timezone(updates["timezone"])
    for key, value in updates.items():
        row = await db.get(Setting, key, with_for_update=True)
        expected = body.versions.get(key)
        current_version = row.version if row else 0
        if expected is not None and expected != current_version:
            raise conflict(
                "VERSION_CONFLICT",
                f"Setting {key} changed; reload it.",
                key=key,
                currentVersion=current_version,
            )
        if row is None:
            db.add(
                Setting(
                    key=key,
                    value=value,
                    value_type=SETTING_DEFAULTS[key][0],
                    version=1,
                    updated_by=auth.user.id,
                )
            )
        else:
            row.value = value
            row.version += 1
            row.updated_by = auth.user.id
    audit.record(
        db,
        action="settings.updated",
        actor_type="user",
        actor_id=auth.user.id,
        target_type="settings",
        request_id=request_id(request),
        metadata={"keys": sorted(updates)},
    )
    await db.flush()
    return await idempotency.finish(
        db, idem, 200, await _settings_out(db, state.settings.public_base_url)
    )


# --- Health and status ------------------------------------------------------------------


@router.get(
    "/health/live", response_model=schemas.HealthOut, tags=["system"], summary="Process liveness"
)
async def live() -> schemas.HealthOut:
    return schemas.HealthOut(status="ok")


@router.get(
    "/health/ready",
    response_model=schemas.HealthOut,
    tags=["system"],
    responses={503: {"model": schemas.HealthOut}},
    summary="Readiness: database reachable and migrated",
)
async def ready(state: AppState = Depends(app_state)) -> Any:
    from fastapi.responses import JSONResponse

    checks: dict[str, str] = {}
    try:
        async with state.sessions() as db:
            await db.execute(text("SELECT 1"))
            revision = await db.scalar(text("SELECT version_num FROM alembic_version"))
        checks["database"] = "ok"
        checks["migrations"] = f"ok ({revision})"
    except Exception as exc:
        checks["database"] = f"unavailable: {type(exc).__name__}"
        body = schemas.HealthOut(status="unavailable", checks=checks)
        return JSONResponse(body.model_dump(by_alias=True), status_code=503)
    return schemas.HealthOut(status="ok", checks=checks)


def _check(group: str, name: str, ok: bool, detail: str, warn: bool = False) -> schemas.StatusCheck:
    status: Literal["passed", "warning", "failed"] = (
        "passed" if ok else ("warning" if warn else "failed")
    )
    return schemas.StatusCheck(
        group=group, name=name, status=status, detail=detail, checked_at=utcnow()
    )


@router.get(
    "/system/status",
    response_model=schemas.SystemStatusOut,
    tags=["system"],
    summary="Device, runtime, storage, and database checks",
)
async def system_status(
    _: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.SystemStatusOut:
    arch = host_architecture()
    checks = [
        _check("device", "architecture", True, arch),
        _check(
            "device",
            "appliance architecture",
            arch == "linux/arm64" or state.settings.profile == "dev",
            "arm64 required for the dgx profile" if arch != "linux/arm64" else "arm64",
            warn=True,
        ),
    ]
    try:
        await db.execute(text("SELECT 1"))
        has_vector = await db.scalar(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
        )
        checks.append(_check("database", "postgresql", True, "Reachable"))
        checks.append(
            _check(
                "database", "pgvector", bool(has_vector), "Installed" if has_vector else "Missing"
            )
        )
    except Exception as exc:
        checks.append(_check("database", "postgresql", False, type(exc).__name__))
    usage = shutil.disk_usage("/")
    free_ratio = usage.free / usage.total
    checks.append(
        _check(
            "storage",
            "disk",
            free_ratio > 0.1,
            f"{usage.free // 2**30} GiB free of {usage.total // 2**30} GiB",
            warn=free_ratio > 0.05,
        )
    )
    runtime: dict[str, Any] = {"adapter": state.settings.runtime_adapter}
    if state.runtime is None:
        checks.append(_check("runtime", "runtime daemon", True, "Fake runtime (development)"))
        checks.append(_check("models", "gpu", True, "Not checked with the fake runtime", warn=True))
    else:
        try:
            capacity = await state.runtime.capacity()
            runtime["capacity"] = capacity
            checks.append(_check("runtime", "runtime daemon", True, "Reachable"))
            gpu = capacity.get("gpu") or {}
            appliance = state.settings.profile == "dgx"
            checks.append(
                _check(
                    "models",
                    "gpu",
                    bool(gpu.get("available")),
                    str(gpu.get("name") or ("Available" if gpu.get("available") else "No GPU")),
                    warn=not appliance,
                )
            )
            docker = capacity.get("docker") or {}
            checks.append(
                _check(
                    "runtime",
                    "nvidia container runtime",
                    bool(docker.get("nvidiaRuntime") or docker.get("cdi")),
                    str(docker.get("detail", "")) or "Checked by the runtime daemon",
                    warn=not appliance,
                )
            )
        except Exception as exc:
            checks.append(
                _check("runtime", "runtime daemon", False, f"Unreachable: {type(exc).__name__}")
            )
    failed = any(c.status == "failed" for c in checks)
    warned = any(c.status == "warning" for c in checks)
    return schemas.SystemStatusOut(
        status="degraded" if failed or warned else "healthy",
        profile=state.settings.profile,
        version=API_VERSION,
        architecture=arch,
        checks=checks,
        runtime=runtime,
    )


# --- Audit ------------------------------------------------------------------------------


@router.get(
    "/audit-events",
    response_model=schemas.Page[schemas.AuditEventOut],
    tags=["audit"],
    summary="Audit history (owner only)",
)
async def list_audit_events(
    action: str | None = Query(
        None, description="Exact action or prefix ending in '*', e.g. auth.*"
    ),
    actor_id: str | None = Query(None, alias="actorId"),
    target_id: str | None = Query(None, alias="targetId"),
    outcome: str | None = Query(None, pattern="^(success|denied|failure)$"),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    _: AuthContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> schemas.Page[schemas.AuditEventOut]:
    limit = clamp_limit(limit)
    stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit + 1)
    if action:
        stmt = stmt.where(
            AuditEvent.action.startswith(action[:-1])
            if action.endswith("*")
            else AuditEvent.action == action
        )
    if actor_id:
        stmt = stmt.where(AuditEvent.actor_id == actor_id)
    if target_id:
        stmt = stmt.where(AuditEvent.target_id == target_id)
    if outcome:
        stmt = stmt.where(AuditEvent.outcome == outcome)
    if since:
        stmt = stmt.where(AuditEvent.created_at >= since)
    if until:
        stmt = stmt.where(AuditEvent.created_at < until)
    after: uuid.UUID | None = decode_cursor(cursor)
    if after:
        stmt = stmt.where(AuditEvent.id < after)
    rows = list((await db.scalars(stmt)).all())
    return schemas.Page(
        items=[schemas.AuditEventOut.model_validate(r) for r in rows[:limit]],
        next_cursor=encode_cursor(rows[limit - 1].id) if len(rows) > limit else None,
    )
