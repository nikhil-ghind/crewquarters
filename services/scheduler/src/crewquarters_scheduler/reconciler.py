"""Run reconciler: enforces time limits, expires inputs, detects lost attempts.

* ``activeTimeoutSeconds`` counts PREPARING, LOADING_MODEL, and RUNNING time only.
* ``maxInputWaitSeconds`` counts WAITING_INPUT time only.
* An attempt whose heartbeat lease expired (hung agent, host restart, or a container
  crash the exit watcher in ``exits.py`` did not see) becomes INTERRUPTED; the owner may
  retry it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_scheduler.worker import on_dead_job
from crewquarters_shared import jobs
from crewquarters_shared.db.models import AgentRun
from crewquarters_shared.runs import service
from crewquarters_shared.runs.states import ACTIVE_CLOCK_STATES, RunState

WATCHED_STATES = [
    RunState.PREPARING,
    RunState.LOADING_MODEL,
    RunState.RUNNING,
    RunState.WAITING_INPUT,
]


@dataclass
class ReconcileReport:
    requeued_jobs: list[int] = field(default_factory=list)
    dead_jobs: int = 0
    active_timeouts: int = 0
    input_timeouts: int = 0
    interrupted: int = 0
    expired_inputs: int = 0


async def tick(session: AsyncSession, now: datetime) -> ReconcileReport:
    report = ReconcileReport()
    requeued, dead = await jobs.reap_expired(session)
    report.requeued_jobs = requeued
    for job in dead:
        report.dead_jobs += 1
        await on_dead_job(
            session, job["type"], job["payload"], {"code": "LEASE_EXPIRED"}, wait_for_lock=False
        )

    runs = (
        await session.scalars(
            select(AgentRun)
            .where(AgentRun.state.in_([s.value for s in WATCHED_STATES]))
            .order_by(AgentRun.state_entered_at)
            .limit(500)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for run in runs:
        if run.state == RunState.WAITING_INPUT:
            if service.wait_seconds(run, now) > run.max_input_wait_seconds:
                await service.fail_run(
                    session,
                    run,
                    "INPUT_TIMEOUT",
                    f"The run waited longer than {run.max_input_wait_seconds}s for input.",
                    retryable=False,
                )
                report.input_timeouts += 1
                continue
            expired = await service.expire_inputs(session, run, now)
            report.expired_inputs += expired
            if expired:
                continue  # state changed this tick; re-evaluate next tick
        if (
            run.state in ACTIVE_CLOCK_STATES
            and service.active_seconds(run, now) > run.active_timeout_seconds
        ):
            await service.fail_run(
                session,
                run,
                "ACTIVE_TIMEOUT",
                f"The run exceeded its {run.active_timeout_seconds}s active-time limit.",
                retryable=False,
            )
            report.active_timeouts += 1
            continue
        attempt = await service.get_attempt(session, run.id, run.current_attempt)
        if (
            attempt is not None
            and attempt.heartbeat_expires_at is not None
            and attempt.heartbeat_expires_at < now
        ):
            await service.fail_run(
                session,
                run,
                "HEARTBEAT_LOST",
                "The agent stopped responding (container exit or platform restart).",
                retryable=True,
                interrupted=True,
            )
            report.interrupted += 1
    return report
