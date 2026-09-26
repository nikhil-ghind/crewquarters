"""Agents starting agents (docs/agent-chaining.md).

A running agent asks the capability broker to start another agent; the broker checks its
``agents.start:<id>`` capability and calls this on the run's behalf. Every rule the owner or the
platform set is enforced here, in one place:

* the owner approved the target for this installation (``permissions.startsAgents``);
* the platform allows it at all (``CQ_AGENT_STARTS_ENABLED``) and within its limits
  (``CQ_AGENT_CHAIN_MAX_DEPTH``, ``CQ_AGENT_STARTS_PER_RUN``);
* the target is one of the owner's own installations, enabled and ready, and its manifest lists
  the ``agent`` trigger; the child runs with *its own* approved permissions, never the caller's;
* a chain never revisits an agent, so A -> B -> A is refused;
* the same ``startKey`` from the same run returns the run the first call created.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_api import views
from crewquarters_api.deps import AppState
from crewquarters_shared import audit
from crewquarters_shared.db.models import AgentInstallation, AgentRun, AgentVersion
from crewquarters_shared.errors import PlatformError, conflict, invalid
from crewquarters_shared.runs import service
from crewquarters_shared.runs.states import ACTIVE_STATES, RunState
from crewquarters_shared.schema_guard import json_size

MAX_INPUT_BYTES = 16 * 1024
TARGETS_KEY = "agentTargets"


async def _agent_id(db: AsyncSession, run: AgentRun) -> str:
    version = await db.get(AgentVersion, run.agent_version_id)
    assert version is not None
    return version.agent_id


async def _ancestors(db: AsyncSession, run: AgentRun) -> list[str]:
    """Agent ids of the run and every run that started it, nearest first."""
    chain = [await _agent_id(db, run)]
    seen = {run.id}
    parent_id = run.parent_run_id
    while parent_id is not None and parent_id not in seen:
        seen.add(parent_id)
        parent = await db.get(AgentRun, parent_id)
        if parent is None:
            break
        chain.append(await _agent_id(db, parent))
        parent_id = parent.parent_run_id
    return chain


async def _target(
    db: AsyncSession, parent: AgentRun, owner_id: uuid.UUID, agent_id: str
) -> AgentInstallation:
    candidates = (
        await db.scalars(
            select(AgentInstallation).where(
                AgentInstallation.user_id == owner_id,
                AgentInstallation.agent_id == agent_id,
                AgentInstallation.deleted_at.is_(None),
                AgentInstallation.enabled.is_(True),
            )
        )
    ).all()
    if not candidates:
        raise conflict(
            "TARGET_NOT_INSTALLED",
            f"{agent_id} is not installed and enabled, so it cannot be started.",
            agentId=agent_id,
        )
    if len(candidates) == 1:
        return candidates[0]
    # Several installations of the same agent: the owner picks one in the caller's config.
    targets = parent.config_snapshot.get(TARGETS_KEY)
    chosen = targets.get(agent_id) if isinstance(targets, dict) else None
    match = next((c for c in candidates if str(c.id) == chosen), None)
    if match is None:
        raise PlatformError(
            "NEEDS_CONFIGURATION",
            f"{agent_id} is installed more than once. Set {TARGETS_KEY}.{agent_id} in this "
            "agent's configuration to the installation that should run.",
            409,
            {"key": f"{TARGETS_KEY}.{agent_id}"},
        )
    return match


async def start_agent_run(
    db: AsyncSession,
    state: AppState,
    run_id: uuid.UUID,
    attempt: int,
    *,
    agent_id: str,
    start_key: str,
    trigger_input: dict[str, Any] | None,
) -> tuple[AgentRun, bool]:
    """Start ``agent_id`` from a running run. Returns the child and whether it was just created."""
    parent = await service.lock_run(db, run_id)
    service.require_attempt(parent, attempt)
    if RunState(parent.state) not in ACTIVE_STATES or parent.cancel_requested_at is not None:
        raise conflict(
            "INVALID_RUN_STATE", f"Cannot start an agent while the run is {parent.state}."
        )

    replay = await db.scalar(
        select(AgentRun).where(AgentRun.parent_run_id == parent.id, AgentRun.start_key == start_key)
    )
    if replay is not None:
        if await _agent_id(db, replay) != agent_id:
            raise conflict("START_KEY_REUSED", "This startKey already started a different agent.")
        return replay, False

    settings = state.settings
    if not settings.agent_starts_enabled:
        raise PlatformError(
            "AGENT_STARTS_DISABLED", "The owner has turned off agents starting agents.", 403
        )
    if agent_id not in (parent.permissions_snapshot.get("startsAgents") or []):
        raise PlatformError(
            "PERMISSION_DENIED", f"This agent was not approved to start {agent_id}.", 403
        )
    if trigger_input is not None and json_size(trigger_input) > MAX_INPUT_BYTES:
        raise invalid("INPUT_TOO_LARGE", f"The input may be at most {MAX_INPUT_BYTES} bytes.")

    chain = await _ancestors(db, parent)
    if agent_id in chain:
        raise conflict("CHAIN_CYCLE", f"{agent_id} is already part of this chain of runs.")
    if len(chain) > settings.agent_chain_max_depth:
        raise conflict(
            "CHAIN_TOO_DEEP",
            f"Agents may start agents at most {settings.agent_chain_max_depth} deep.",
        )
    started = await db.scalar(
        select(func.count()).select_from(AgentRun).where(AgentRun.parent_run_id == parent.id)
    )
    if int(started or 0) >= settings.agent_starts_per_run:
        raise conflict(
            "START_LIMIT_REACHED",
            f"One run may start at most {settings.agent_starts_per_run} other runs.",
        )

    parent_installation = await db.get(AgentInstallation, parent.installation_id)
    assert parent_installation is not None
    target = await _target(db, parent, parent_installation.user_id, agent_id)
    version = await db.get(AgentVersion, target.agent_version_id)
    assert version is not None
    if "agent" not in version.manifest["spec"]["triggers"]:
        raise conflict(
            "TRIGGER_NOT_SUPPORTED", f"{agent_id} does not accept being started by another agent."
        )
    readiness = await views.readiness(target, version, state.models, state.connections)
    if not readiness.ready:
        raise conflict(
            "INSTALLATION_NOT_READY",
            f"{agent_id} is not ready to run.",
            checks=[c.model_dump(by_alias=True) for c in readiness.checks if c.status != "ok"],
        )
    created = await service.create_run(
        db,
        target,
        version,
        trigger="agent",
        parent_run_id=parent.id,
        start_key=start_key,
        trigger_input=trigger_input,
    )
    audit.record(
        db,
        action="run.created",
        actor_type="service",  # the broker, on behalf of the parent run
        actor_id=parent.id,
        target_type="run",
        target_id=created.run.id,
        metadata={
            "installationId": str(target.id),
            "trigger": "agent",
            "parentRunId": str(parent.id),
            "agentId": agent_id,
        },
    )
    return created.run, created.created
