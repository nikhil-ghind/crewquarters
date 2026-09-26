"""Builders that turn ORM rows into API response models, plus installation readiness."""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_api import schemas
from crewquarters_api.catalog import compatibility_issues
from crewquarters_shared import capability
from crewquarters_shared.clients import ConnectionStatusClient, ModelStatusClient
from crewquarters_shared.cron import next_occurrences
from crewquarters_shared.db.models import (
    AgentCatalogEntry,
    AgentInstallation,
    AgentRun,
    AgentVersion,
    InputRequest,
    Schedule,
)
from crewquarters_shared.errors import PlatformError
from crewquarters_shared.manifest import validate_config


def version_out(version: AgentVersion) -> schemas.AgentVersionOut:
    spec = version.manifest["spec"]
    issues = compatibility_issues(version.manifest)
    return schemas.AgentVersionOut(
        id=version.id,
        version=version.version,
        image=version.image_ref,
        image_digest=version.image_digest,
        sdk_protocol=version.sdk_protocol,
        architectures=version.architectures,
        triggers=spec["triggers"],
        permissions=spec["permissions"],
        resources=spec["resources"],
        configuration_schema=spec.get("configurationSchema", {}),
        result_schema=spec.get("resultSchema"),
        compatible=not issues,
        compatibility_issues=issues,
        created_at=version.created_at,
    )


def occurrence_out(at: datetime, timezone: str) -> schemas.OccurrenceOut:
    local = at.astimezone(ZoneInfo(timezone))
    return schemas.OccurrenceOut(
        at=at, local=local.isoformat(), zone_abbreviation=local.tzname() or timezone
    )


def schedule_out(
    schedule: Schedule, agent_name: str, readiness: schemas.Readiness
) -> schemas.ScheduleOut:
    upcoming: list[schemas.OccurrenceOut] = []
    if schedule.enabled and schedule.next_run_at is not None:
        first = schedule.next_run_at
        rest = next_occurrences(schedule.cron, schedule.timezone, first, 2)
        upcoming = [occurrence_out(t, schedule.timezone) for t in [first, *rest]]
    return schemas.ScheduleOut(
        id=schedule.id,
        installation_id=schedule.installation_id,
        agent_name=agent_name,
        ready=readiness.ready,
        blockers=[c for c in readiness.checks if c.status != "ok"],
        cron=schedule.cron,
        timezone=schedule.timezone,
        misfire_policy=schedule.misfire_policy,
        enabled=schedule.enabled,
        next_run_at=schedule.next_run_at,
        next_occurrences=upcoming,
        last_fired_at=schedule.last_fired_at,
        last_run_id=schedule.last_run_id,
        version=schedule.version,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


async def readiness(
    installation: AgentInstallation,
    version: AgentVersion,
    models: ModelStatusClient,
    connections: ConnectionStatusClient,
) -> schemas.Readiness:
    manifest = version.manifest
    perms = manifest["spec"]["permissions"]
    checks: list[schemas.ReadinessCheck] = []

    def add(
        name: str, ok: bool, detail: str, resource: str | None = None, missing: bool = False
    ) -> None:
        status = "ok" if ok else ("missing" if missing else "needs_attention")
        checks.append(
            schemas.ReadinessCheck(name=name, status=status, detail=detail, resource=resource)
        )

    add(
        "enabled",
        installation.enabled,
        "Enabled" if installation.enabled else "This agent is disabled.",
    )
    add(
        "permissions",
        not installation.needs_reapproval,
        "Permissions approved"
        if not installation.needs_reapproval
        else "This version requests different permissions. Review and approve them.",
    )
    issues = compatibility_issues(manifest)
    add("architecture", not issues, "Compatible with this device" if not issues else issues[0])
    try:
        validate_config(manifest, installation.config, installation.model_bindings)
        add("configuration", True, "Configuration is valid")
    except PlatformError as exc:
        add("configuration", False, exc.message, missing=True)

    status_by_provider = {c["provider"]: c for c in await connections.list_connections()}
    required: dict[str, list[str]] = {}
    for scope in perms["connectors"].get("google", []):
        required.setdefault("google", []).append(scope)
    for op in perms["connectors"].get("twilio", []):
        required.setdefault("twilio", []).append(op)
    for op in perms["connectors"].get("github", []):
        required.setdefault("github", []).append(op)
    for provider in perms.get("cloudProviders", []):
        required.setdefault(provider, []).append(f"cloud.{provider}")
    for provider, scopes in required.items():
        conn = status_by_provider.get(provider)
        connected = conn is not None and conn["status"] == "CONNECTED"
        granted = set(conn["grantedCapabilities"]) if conn else set()
        missing_scopes = [s for s in scopes if s not in granted]
        if conn is not None and conn["status"] == "UNKNOWN":
            add(
                "connection",
                False,
                f"Cannot check {provider.title()}: the capability broker is unavailable.",
                provider,
            )
        elif not connected:
            add(
                "connection",
                False,
                f"Connect {provider.title()} to run this agent.",
                provider,
                missing=True,
            )
        elif missing_scopes:
            add(
                "connection",
                False,
                f"Grant {', '.join(missing_scopes)} in {provider.title()}.",
                provider,
            )
        else:
            add("connection", True, f"{provider.title()} connected", provider)

    for variant in sorted(set(installation.model_bindings.values())):
        if not variant.startswith("local."):
            continue
        model = await models.get_model(variant)
        installed = model is not None and model.get("downloadState") == "INSTALLED"
        add(
            "model",
            installed,
            f"{variant} is installed" if installed else f"Install the {variant} model.",
            variant,
            missing=True,
        )
    return schemas.Readiness(ready=all(c.status == "ok" for c in checks), checks=checks)


async def installation_out(
    db: AsyncSession,
    installation: AgentInstallation,
    models: ModelStatusClient,
    connections: ConnectionStatusClient,
) -> schemas.InstallationOut:
    version = await db.get(AgentVersion, installation.agent_version_id)
    entry = await db.get(AgentCatalogEntry, installation.agent_id)
    assert version is not None and entry is not None
    return schemas.InstallationOut(
        id=installation.id,
        agent_id=installation.agent_id,
        agent_name=entry.name,
        agent_version=version.version,
        agent_version_id=version.id,
        config=installation.config,
        requested_permissions=version.manifest["spec"]["permissions"],
        approved_permissions=installation.approved_permissions,
        capabilities=capability.capabilities_from_permissions(
            installation.approved_permissions, installation.model_bindings
        ),
        model_bindings=installation.model_bindings,
        needs_reapproval=installation.needs_reapproval,
        enabled=installation.enabled,
        version=installation.version,
        readiness=await readiness(installation, version, models, connections),
        created_at=installation.created_at,
        updated_at=installation.updated_at,
    )


async def run_out(db: AsyncSession, run: AgentRun) -> schemas.RunOut:
    row = (
        await db.execute(
            select(AgentVersion.version, AgentCatalogEntry.agent_id, AgentCatalogEntry.name)
            .join(AgentCatalogEntry, AgentCatalogEntry.agent_id == AgentVersion.agent_id)
            .where(AgentVersion.id == run.agent_version_id)
        )
    ).one()
    pending = await db.scalar(
        select(func.count())
        .select_from(InputRequest)
        .where(InputRequest.run_id == run.id, InputRequest.state == "pending")
    )
    return _run_out(run, row.version, row.agent_id, row.name, int(pending or 0))


def _run_out(run: AgentRun, version: str, agent_id: str, name: str, pending: int) -> schemas.RunOut:
    return schemas.RunOut(
        id=run.id,
        installation_id=run.installation_id,
        agent_id=agent_id,
        agent_name=name,
        agent_version=version,
        trigger=run.trigger,
        parent_run_id=run.parent_run_id,
        schedule_id=run.schedule_id,
        scheduled_for=run.scheduled_for,
        state=run.state,
        current_attempt=run.current_attempt,
        result=run.result,
        error=run.error,
        retryable=run.retryable,
        cancel_requested=run.cancel_requested_at is not None,
        acknowledged_at=run.acknowledged_at,
        active_seconds_used=round(run.active_seconds_used, 3),
        input_wait_seconds_used=round(run.input_wait_seconds_used, 3),
        active_timeout_seconds=run.active_timeout_seconds,
        max_input_wait_seconds=run.max_input_wait_seconds,
        uses_cloud=bool(run.permissions_snapshot.get("cloudProviders")),
        pending_input_count=pending,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        updated_at=run.updated_at,
    )


async def runs_out(db: AsyncSession, runs: list[AgentRun]) -> list[schemas.RunOut]:
    if not runs:
        return []
    version_ids = {r.agent_version_id for r in runs}
    meta = {
        row.id: row
        for row in (
            await db.execute(
                select(
                    AgentVersion.id,
                    AgentVersion.version,
                    AgentCatalogEntry.agent_id,
                    AgentCatalogEntry.name,
                )
                .join(AgentCatalogEntry, AgentCatalogEntry.agent_id == AgentVersion.agent_id)
                .where(AgentVersion.id.in_(version_ids))
            )
        )
    }
    pending_rows = (
        await db.execute(
            select(InputRequest.run_id, func.count())
            .where(InputRequest.run_id.in_([r.id for r in runs]), InputRequest.state == "pending")
            .group_by(InputRequest.run_id)
        )
    ).all()
    pending: dict[uuid.UUID, int] = {row[0]: int(row[1]) for row in pending_rows}
    return [
        _run_out(
            r,
            meta[r.agent_version_id].version,
            meta[r.agent_version_id].agent_id,
            meta[r.agent_version_id].name,
            int(pending.get(r.id, 0)),
        )
        for r in runs
    ]


def input_out(request: InputRequest, agent_name: str | None) -> schemas.InputRequestOut:
    return schemas.InputRequestOut(
        id=request.id,
        run_id=request.run_id,
        agent_name=agent_name,
        key=request.key,
        title=request.title,
        prompt=request.prompt,
        schema=request.schema,
        preview=request.preview,
        state=request.state,
        answer=request.answer if request.state == "answered" else None,
        deadline=request.deadline,
        version=request.version,
        created_at=request.created_at,
        answered_at=request.answered_at,
    )
