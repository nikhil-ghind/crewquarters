"""Catalog and agent installations (PLAN.md sections 5.1, 10.4, 13.6)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_api import catalog, idempotency, schemas, views
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
    decode_text_cursor,
    encode_cursor,
    encode_text_cursor,
)
from crewquarters_shared import audit, capability
from crewquarters_shared.db.models import (
    AgentCatalogEntry,
    AgentInstallation,
    AgentRun,
    AgentVersion,
    Schedule,
)
from crewquarters_shared.errors import conflict, invalid, not_found
from crewquarters_shared.manifest import resolve_model_bindings, validate_config
from crewquarters_shared.runs.states import FINAL_STATES, RunState
from crewquarters_shared.timeutil import utcnow

router = APIRouter(tags=["agents"])

ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": schemas.ErrorResponse},
    409: {"model": schemas.ErrorResponse},
    422: {"model": schemas.ErrorResponse},
}


def _normalize_permissions(perms: dict[str, Any]) -> dict[str, Any]:
    connectors = perms.get("connectors") or {}
    return {
        "llmProfiles": sorted(perms.get("llmProfiles") or []),
        "knowledge": sorted(perms.get("knowledge") or []),
        "connectors": {
            "google": sorted(connectors.get("google") or []),
            "twilio": sorted(connectors.get("twilio") or []),
        },
        "cloudProviders": sorted(perms.get("cloudProviders") or []),
        "userInput": bool(perms.get("userInput")),
    }


def permissions_match(approved: dict[str, Any], requested: dict[str, Any]) -> bool:
    return _normalize_permissions(approved) == _normalize_permissions(requested)


async def _versions(db: AsyncSession, agent_id: str) -> list[AgentVersion]:
    return list(
        (
            await db.scalars(
                select(AgentVersion)
                .where(AgentVersion.agent_id == agent_id)
                .order_by(AgentVersion.created_at.desc())
            )
        ).all()
    )


async def _catalog_out(
    db: AsyncSession, entry: AgentCatalogEntry, user_id: uuid.UUID
) -> schemas.CatalogAgentOut:
    versions = await _versions(db, entry.agent_id)
    current = next(v for v in versions if v.version == entry.current_version)
    installed = await db.scalar(
        select(AgentInstallation.id).where(
            AgentInstallation.agent_id == entry.agent_id,
            AgentInstallation.user_id == user_id,
            AgentInstallation.deleted_at.is_(None),
        )
    )
    return schemas.CatalogAgentOut(
        agent_id=entry.agent_id,
        name=entry.name,
        summary=entry.summary,
        publisher=entry.publisher,
        source=entry.source,
        trust_status=entry.trust_status,
        current_version=entry.current_version,
        versions=[v.version for v in versions],
        latest=views.version_out(current),
        installed=installed is not None,
    )


# --- Catalog ------------------------------------------------------------------------


@router.get(
    "/catalog/agents",
    response_model=schemas.Page[schemas.CatalogAgentOut],
    summary="List catalog agents (ordered by agent id)",
)
async def list_catalog(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
) -> schemas.Page[schemas.CatalogAgentOut]:
    limit = clamp_limit(limit)
    stmt = select(AgentCatalogEntry).order_by(AgentCatalogEntry.agent_id).limit(limit + 1)
    after = decode_text_cursor(cursor)
    if after is not None:
        stmt = stmt.where(AgentCatalogEntry.agent_id > after)
    entries = list((await db.scalars(stmt)).all())
    items = [await _catalog_out(db, e, auth.user.id) for e in entries[:limit]]
    more = len(entries) > limit
    return schemas.Page(
        items=items, next_cursor=encode_text_cursor(entries[limit - 1].agent_id) if more else None
    )


@router.get(
    "/catalog/agents/{agent_id}",
    response_model=schemas.CatalogAgentOut,
    responses=ERRORS,
    summary="Get a catalog agent",
)
async def get_catalog_agent(
    agent_id: str, auth: AuthContext = Depends(current_auth), db: AsyncSession = Depends(get_db)
) -> schemas.CatalogAgentOut:
    entry = await db.get(AgentCatalogEntry, agent_id)
    if entry is None:
        raise not_found("Agent", agent_id)
    return await _catalog_out(db, entry, auth.user.id)


@router.get(
    "/catalog/agents/{agent_id}/versions/{version}",
    response_model=schemas.AgentVersionOut,
    responses=ERRORS,
    summary="Get one immutable agent version",
)
async def get_catalog_version(
    agent_id: str,
    version: str,
    _: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
) -> schemas.AgentVersionOut:
    row = await db.scalar(
        select(AgentVersion).where(
            AgentVersion.agent_id == agent_id, AgentVersion.version == version
        )
    )
    if row is None:
        raise not_found("Agent version", f"{agent_id}@{version}")
    return views.version_out(row)


@router.post(
    "/catalog/agents/import",
    response_model=schemas.CatalogAgentOut,
    status_code=201,
    responses=ERRORS,
    summary="Import a locally built agent (crewctl publish --target local)",
)
async def import_agent(
    body: schemas.CatalogImportIn,
    request: Request,
    auth: AuthContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> Response:
    idem = await idempotency.begin(db, request, auth.user.id, body.model_dump(by_alias=True))
    if idem.replay:
        return idem.replay
    version = await catalog.upsert_manifest(db, body.manifest, source="imported")
    audit.record(
        db,
        action="catalog.import",
        actor_type="user",
        actor_id=auth.user.id,
        target_type="agent_version",
        target_id=version.id,
        request_id=request_id(request),
        metadata={
            "agentId": version.agent_id,
            "version": version.version,
            "digest": version.image_digest,
        },
    )
    await db.flush()
    entry = await db.get(AgentCatalogEntry, version.agent_id)
    assert entry is not None
    await db.refresh(entry)
    return await idempotency.finish(db, idem, 201, await _catalog_out(db, entry, auth.user.id))


# --- Installations --------------------------------------------------------------------


async def _load_installation(
    db: AsyncSession, installation_id: uuid.UUID, user_id: uuid.UUID, lock: bool = False
) -> AgentInstallation:
    stmt = select(AgentInstallation).where(
        AgentInstallation.id == installation_id,
        AgentInstallation.user_id == user_id,
        AgentInstallation.deleted_at.is_(None),
    )
    if lock:
        stmt = stmt.with_for_update()
    installation = await db.scalar(stmt)
    if installation is None:
        raise not_found("Installation", installation_id)
    return installation


@router.get(
    "/agent-installations",
    response_model=schemas.Page[schemas.InstallationOut],
    summary="List installed agents",
)
async def list_installations(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.Page[schemas.InstallationOut]:
    limit = clamp_limit(limit)
    stmt = (
        select(AgentInstallation)
        .where(AgentInstallation.user_id == auth.user.id, AgentInstallation.deleted_at.is_(None))
        .order_by(AgentInstallation.id.desc())
        .limit(limit + 1)
    )
    after = decode_cursor(cursor)
    if after is not None:
        stmt = stmt.where(AgentInstallation.id < after)
    rows = list((await db.scalars(stmt)).all())
    items = [
        await views.installation_out(db, i, state.models, state.connections) for i in rows[:limit]
    ]
    return schemas.Page(
        items=items, next_cursor=encode_cursor(rows[limit - 1].id) if len(rows) > limit else None
    )


@router.post(
    "/agent-installations",
    response_model=schemas.InstallationOut,
    status_code=201,
    responses=ERRORS,
    summary="Install an agent, approving its exact permissions",
)
async def create_installation(
    body: schemas.InstallationCreateIn,
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    idem = await idempotency.begin(db, request, auth.user.id, body.model_dump(by_alias=True))
    if idem.replay:
        return idem.replay
    entry = await db.get(AgentCatalogEntry, body.agent_id)
    if entry is None:
        raise not_found("Agent", body.agent_id)
    wanted = body.version or entry.current_version
    version = await db.scalar(
        select(AgentVersion).where(
            AgentVersion.agent_id == entry.agent_id, AgentVersion.version == wanted
        )
    )
    if version is None:
        raise not_found("Agent version", f"{entry.agent_id}@{wanted}")
    issues = catalog.compatibility_issues(version.manifest)
    if issues:
        raise conflict("INCOMPATIBLE_AGENT", issues[0], issues=issues)
    requested = version.manifest["spec"]["permissions"]
    if not permissions_match(body.approved_permissions, requested):
        raise invalid(
            "PERMISSIONS_NOT_APPROVED",
            "Approve exactly the permissions this agent version requests.",
            requested=requested,
        )
    bindings = resolve_model_bindings(requested["llmProfiles"], body.model_bindings)
    config = validate_config(version.manifest, body.config, bindings)
    installation = AgentInstallation(
        user_id=auth.user.id,
        agent_id=entry.agent_id,
        agent_version_id=version.id,
        approved_version_id=version.id,
        config=config,
        approved_permissions=requested,
        model_bindings=bindings,
        needs_reapproval=False,
        enabled=body.enabled,
        version=1,
    )
    db.add(installation)
    await db.flush()
    audit.record(
        db,
        action="agent.installed",
        actor_type="user",
        actor_id=auth.user.id,
        target_type="agent_installation",
        target_id=installation.id,
        request_id=request_id(request),
        metadata={
            "agentId": entry.agent_id,
            "version": version.version,
            "digest": version.image_digest,
            "capabilities": capability.capabilities_from_permissions(requested, bindings),
        },
    )
    await db.refresh(installation)
    out = await views.installation_out(db, installation, state.models, state.connections)
    return await idempotency.finish(db, idem, 201, out)


@router.get(
    "/agent-installations/{installation_id}",
    response_model=schemas.InstallationOut,
    responses=ERRORS,
    summary="Get an installed agent with readiness",
)
async def get_installation(
    installation_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.InstallationOut:
    installation = await _load_installation(db, installation_id, auth.user.id)
    return await views.installation_out(db, installation, state.models, state.connections)


@router.patch(
    "/agent-installations/{installation_id}",
    response_model=schemas.InstallationOut,
    responses=ERRORS,
    summary="Update configuration, enablement, version, or permission approval",
)
async def update_installation(
    installation_id: uuid.UUID,
    body: schemas.InstallationPatchIn,
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    idem = await idempotency.begin(
        db, request, auth.user.id, {"id": str(installation_id), **body.model_dump(by_alias=True)}
    )
    if idem.replay:
        return idem.replay
    installation = await _load_installation(db, installation_id, auth.user.id, lock=True)
    if installation.version != body.version:
        raise conflict(
            "VERSION_CONFLICT",
            "The installation changed; reload it.",
            currentVersion=installation.version,
        )
    version = await db.get(AgentVersion, installation.agent_version_id)
    assert version is not None
    changes: dict[str, Any] = {}

    if body.agent_version is not None and body.agent_version != version.version:
        new_version = await db.scalar(
            select(AgentVersion).where(
                AgentVersion.agent_id == installation.agent_id,
                AgentVersion.version == body.agent_version,
            )
        )
        if new_version is None:
            raise not_found("Agent version", f"{installation.agent_id}@{body.agent_version}")
        issues = catalog.compatibility_issues(new_version.manifest)
        if issues:
            raise conflict("INCOMPATIBLE_AGENT", issues[0], issues=issues)
        changes["agentVersion"] = {"from": version.version, "to": new_version.version}
        version = new_version
        installation.agent_version_id = new_version.id
        requested = new_version.manifest["spec"]["permissions"]
        if not permissions_match(installation.approved_permissions, requested):
            installation.needs_reapproval = True
            changes["needsReapproval"] = True

    requested = version.manifest["spec"]["permissions"]
    if body.approved_permissions is not None:
        if not permissions_match(body.approved_permissions, requested):
            raise invalid(
                "PERMISSIONS_NOT_APPROVED",
                "Approve exactly the permissions this agent version requests.",
                requested=requested,
            )
        installation.approved_permissions = requested
        installation.approved_version_id = version.id
        installation.needs_reapproval = False
        changes["permissionsApproved"] = capability.capabilities_from_permissions(requested)

    choices = body.model_bindings
    if choices is None:
        choices = {
            k: v for k, v in installation.model_bindings.items() if k in requested["llmProfiles"]
        }
    installation.model_bindings = resolve_model_bindings(requested["llmProfiles"], choices)
    config = body.config if body.config is not None else installation.config
    installation.config = validate_config(version.manifest, config, installation.model_bindings)
    if body.config is not None:
        changes["config"] = True
    if body.enabled is not None and body.enabled != installation.enabled:
        installation.enabled = body.enabled
        changes["enabled"] = body.enabled
    installation.version += 1

    action = "permissions.approved" if "permissionsApproved" in changes else "agent.updated"
    audit.record(
        db,
        action=action,
        actor_type="user",
        actor_id=auth.user.id,
        target_type="agent_installation",
        target_id=installation.id,
        request_id=request_id(request),
        metadata=changes,
    )
    await db.flush()
    await db.refresh(installation)
    out = await views.installation_out(db, installation, state.models, state.connections)
    return await idempotency.finish(db, idem, 200, out)


@router.delete(
    "/agent-installations/{installation_id}",
    status_code=204,
    responses=ERRORS,
    summary="Uninstall an agent (soft delete; blocked while runs are active)",
)
async def delete_installation(
    installation_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> Response:
    idem = await idempotency.begin(db, request, auth.user.id, {"id": str(installation_id)})
    if idem.replay:
        return idem.replay
    installation = await _load_installation(db, installation_id, auth.user.id, lock=True)
    active = await db.scalar(
        select(AgentRun.id)
        .where(
            AgentRun.installation_id == installation.id,
            AgentRun.state.not_in(
                [s.value for s in FINAL_STATES]
                + [RunState.FAILED.value, RunState.INTERRUPTED.value]
            ),
        )
        .limit(1)
    )
    if active is not None:
        raise conflict(
            "INSTALLATION_IN_USE",
            "Cancel or finish active runs before uninstalling.",
            runId=str(active),
        )
    now = utcnow()
    installation.deleted_at = now
    installation.enabled = False
    schedules = (
        await db.scalars(
            select(Schedule).where(
                Schedule.installation_id == installation.id, Schedule.deleted_at.is_(None)
            )
        )
    ).all()
    for schedule in schedules:
        schedule.deleted_at = now
        schedule.enabled = False
    audit.record(
        db,
        action="agent.uninstalled",
        actor_type="user",
        actor_id=auth.user.id,
        target_type="agent_installation",
        target_id=installation.id,
        request_id=request_id(request),
        metadata={"agentId": installation.agent_id, "schedulesRemoved": len(schedules)},
    )
    return await idempotency.finish(db, idem, 204, None)
