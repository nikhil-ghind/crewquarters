"""Backups and diagnostics (PLAN.md sections 13.13 and 15.2). Owner only.

Restore is deliberately not an API operation: it replaces the whole database and must
run with the platform stopped, so it is ``crewquarters backup restore`` on the device
(docs/runbooks/backup-restore.md).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_api import backups, diagnostics, idempotency, schemas
from crewquarters_api.deps import (
    AppState,
    AuthContext,
    app_state,
    get_db,
    request_id,
    require_owner,
)
from crewquarters_api.pagination import clamp_limit, decode_text_cursor, encode_text_cursor
from crewquarters_shared import audit, backup
from crewquarters_shared.errors import PlatformError, conflict, not_found
from crewquarters_shared.logs import recent_logs

router = APIRouter(tags=["system"])

ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": schemas.ErrorResponse},
    404: {"model": schemas.ErrorResponse},
    409: {"model": schemas.ErrorResponse},
}
INCLUDES = [
    "The database: settings, agents, schedules, runs, requests, chat history, audit history",
    "Uploaded knowledge documents",
    "Connection records, with their secrets still encrypted",
]
EXCLUDES = [
    "The device master key (only `crewquarters backup create --include-master-key` on the "
    "device adds it)",
    "Model files (download them again from Models)",
]


def _not_configured() -> PlatformError:
    return PlatformError(
        "BACKUPS_NOT_CONFIGURED",
        "Backups are not configured on this device (CQ_BACKUP_DIR). Use "
        "`crewquarters backup create` on the device.",
        409,
    )


@router.get(
    "/system/backups",
    response_model=schemas.BackupPage,
    summary="Backups: queued, running, failed, and archives on the device (newest first)",
)
async def list_backups(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    _: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.BackupPage:
    settings = state.settings
    # Names start with the UTC time, so name order is time order: newest first.
    items = sorted(await backups.list_backups(db, settings), key=lambda b: b["id"], reverse=True)
    after = decode_text_cursor(cursor)
    if after is not None:
        items = [b for b in items if b["id"] < after]
    limit = clamp_limit(limit)
    page = items[:limit]
    nxt = encode_text_cursor(page[-1]["id"]) if len(items) > limit and page else None
    return schemas.BackupPage(
        items=[schemas.BackupOut.model_validate(b) for b in page],
        next_cursor=nxt,
        enabled=settings.backup_dir is not None,
        location=str(settings.backup_dir) if settings.backup_dir else None,
        retention=settings.backup_retention,
        includes=INCLUDES,
        excludes=EXCLUDES,
    )


@router.post(
    "/system/backups",
    response_model=schemas.BackupOut,
    status_code=202,
    responses=ERRORS,
    summary="Create a backup (runs as a job; never includes the device master key)",
)
async def create_backup(
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if state.settings.backup_dir is None:
        raise _not_configured()
    idem = await idempotency.begin(db, request, auth.user.id, {"action": "backup"})
    if idem.replay:
        return idem.replay
    job_id, name = await backups.enqueue(db, requested_by=str(auth.user.id))
    if job_id is None:
        live = await backups.live_job(db)
        raise conflict(
            "BACKUP_IN_PROGRESS",
            "A backup is already queued or running. Wait for it to finish.",
            backupId=live.payload.get("name") if live else None,
        )
    audit.record(
        db,
        action="system.backup_requested",
        actor_type="user",
        actor_id=auth.user.id,
        target_type="backup",
        target_id=name,
        request_id=request_id(request),
    )
    await db.flush()
    item = next(i for i in await backups.list_backups(db, state.settings) if i["id"] == name)
    return await idempotency.finish(db, idem, 202, schemas.BackupOut.model_validate(item))


@router.get(
    "/system/backups/{backup_id}/download",
    responses={
        200: {
            "description": "The backup archive (gzip-compressed tar).",
            "content": {"application/gzip": {"schema": {"type": "string", "format": "binary"}}},
        },
        **ERRORS,
    },
    summary="Download a backup archive (owner only; audited)",
)
async def download_backup(
    backup_id: str,
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    directory = state.settings.backup_dir
    if directory is None:
        raise _not_configured()
    if not backup.NAME_RE.match(backup_id):
        raise not_found("Backup", backup_id)
    path = backup.archive_path(directory, backup_id)
    if not path.is_file():
        raise not_found("Backup", backup_id)
    try:
        manifest = backup.read_manifest(directory, backup_id)
    except backup.BackupError:
        raise PlatformError(
            "BACKUP_UNREADABLE",
            "This backup cannot be read by the platform. Copy it from the device instead.",
            403,
        ) from None
    if manifest.get("includesMasterKey"):
        audit.record(
            db,
            action="system.backup_downloaded",
            actor_type="user",
            actor_id=auth.user.id,
            target_type="backup",
            target_id=backup_id,
            outcome="denied",
            request_id=request_id(request),
        )
        await db.commit()
        raise PlatformError(
            "BACKUP_CONTAINS_MASTER_KEY",
            "This backup contains the device master key and can only be copied on the device.",
            403,
        )
    audit.record(
        db,
        action="system.backup_downloaded",
        actor_type="user",
        actor_id=auth.user.id,
        target_type="backup",
        target_id=backup_id,
        request_id=request_id(request),
        metadata={"bytes": path.stat().st_size},
    )
    await db.commit()
    return FileResponse(
        path,
        media_type="application/gzip",
        filename=path.name,
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/system/diagnostics",
    responses={
        200: {
            "description": "Redacted diagnostics bundle (zip).",
            "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
        },
        403: {"model": schemas.ErrorResponse},
    },
    summary="Download a redacted diagnostics bundle (owner only; audited)",
)
async def download_diagnostics(
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    audit.record(
        db,
        action="system.diagnostics_downloaded",
        actor_type="user",
        actor_id=auth.user.id,
        request_id=request_id(request),
    )
    await db.commit()
    await db.close()
    bundle = await diagnostics.build_bundle(
        diagnostics.Sources(
            settings=state.settings,
            sessions=state.sessions,
            models=state.models,
            connections=state.connections,
            runtime=state.runtime,
            log_lines=recent_logs()[-state.settings.diagnostics_log_lines :],
        )
    )
    name = diagnostics.bundle_filename()
    return Response(
        bundle,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )
