"""Schedule evaluation (PLAN.md section 7.1).

``tick`` is called about once per second by the elected leader. For each due
schedule it creates at most one run per occurrence, atomically with advancing
``next_run_at``. The unique ``(schedule_id, scheduled_for)`` index is the final
duplicate defense if two ticks ever overlap.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_shared import audit
from crewquarters_shared.cron import latest_occurrence_at_or_before, next_occurrence
from crewquarters_shared.db.models import AgentInstallation, AgentVersion, Schedule
from crewquarters_shared.runs import service

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FireResult:
    schedule_id: uuid.UUID
    scheduled_for: datetime
    run_id: uuid.UUID | None
    outcome: str  # fired | duplicate | misfire_skipped | installation_unavailable


async def tick(
    session: AsyncSession, now: datetime, misfire_grace_seconds: int
) -> list[FireResult]:
    due = (
        await session.scalars(
            select(Schedule)
            .where(
                Schedule.enabled.is_(True),
                Schedule.deleted_at.is_(None),
                Schedule.next_run_at.is_not(None),
                Schedule.next_run_at <= now,
            )
            .order_by(Schedule.next_run_at)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
    ).all()
    results: list[FireResult] = []
    for schedule in due:
        results.append(await _fire(session, schedule, now, misfire_grace_seconds))
    return results


async def _fire(
    session: AsyncSession, schedule: Schedule, now: datetime, grace_seconds: int
) -> FireResult:
    assert schedule.next_run_at is not None
    due_at = schedule.next_run_at
    late = now - due_at > timedelta(seconds=grace_seconds)
    scheduled_for = (
        latest_occurrence_at_or_before(schedule.cron, schedule.timezone, due_at, now)
        if late
        else due_at
    )
    schedule.next_run_at = next_occurrence(
        schedule.cron, schedule.timezone, now if late else scheduled_for
    )

    if late and schedule.misfire_policy == "skip":
        audit.record(
            session,
            action="schedule.misfire_skipped",
            actor_type="system",
            target_type="schedule",
            target_id=schedule.id,
            metadata={"missedFrom": due_at.isoformat(), "missedThrough": scheduled_for.isoformat()},
        )
        return FireResult(schedule.id, scheduled_for, None, "misfire_skipped")

    installation = await session.get(AgentInstallation, schedule.installation_id)
    if (
        installation is None
        or installation.deleted_at is not None
        or not installation.enabled
        or installation.needs_reapproval
    ):
        audit.record(
            session,
            action="schedule.skipped",
            actor_type="system",
            target_type="schedule",
            target_id=schedule.id,
            outcome="failure",
            metadata={
                "reason": "installation_unavailable",
                "scheduledFor": scheduled_for.isoformat(),
            },
        )
        return FireResult(schedule.id, scheduled_for, None, "installation_unavailable")

    version = await session.get(AgentVersion, installation.agent_version_id)
    assert version is not None
    created = await service.create_run(
        session,
        installation,
        version,
        trigger="schedule",
        schedule_id=schedule.id,
        scheduled_for=scheduled_for,
    )
    schedule.last_fired_at = now
    schedule.last_run_id = created.run.id
    if late:
        audit.record(
            session,
            action="schedule.misfire_fired_once",
            actor_type="system",
            target_type="schedule",
            target_id=schedule.id,
            metadata={"missedFrom": due_at.isoformat(), "firedFor": scheduled_for.isoformat()},
        )
    outcome = "fired" if created.created else "duplicate"
    return FireResult(schedule.id, scheduled_for, created.run.id, outcome)
