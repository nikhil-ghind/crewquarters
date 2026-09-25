"""Exit watcher: notices agent containers that exited, directly from the runtime.

The heartbeat lease (reconciler) catches every lost attempt eventually, but only after the
lease: up to ``CQ_HEARTBEAT_TIMEOUT_SECONDS`` after the handshake and
``CQ_PREPARE_TIMEOUT_SECONDS`` (600 s) before it, and it cannot tell a crash from an
out-of-memory kill. The leader therefore asks the runtime about every attempt that has a
container and no recorded exit code, every ``CQ_EXIT_WATCH_INTERVAL_SECONDS``
(ADR 0006, revision 1):

* an exited container's exit code is written to ``run_attempts.exit_code``;
* if the run is still active on that attempt, it fails with ``AGENT_OUT_OF_MEMORY``,
  ``AGENT_EXITED`` or ``AGENT_EXITED_WITHOUT_RESULT`` (see ``service.record_container_exit``).

Runtime calls are made with no database transaction open and no run row locked; the run is
locked only afterwards, to record what was observed. A container the runtime no longer knows
(``missing``) is left to the heartbeat lease.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_shared.db.models import AgentRun, RunAttempt
from crewquarters_shared.runs import service
from crewquarters_shared.runs.states import RunState
from crewquarters_shared.runtime import RuntimeAdapter, RuntimeStatus

log = logging.getLogger(__name__)

WATCHED_STATES = [
    RunState.PREPARING,
    RunState.LOADING_MODEL,
    RunState.RUNNING,
    RunState.WAITING_INPUT,
    RunState.CANCELLING,
]
# Attempts that already ended (a result was posted, or the run failed or was cancelled) are
# still asked about for a while, to record the exit code of a container that exits after it.
RECENTLY_ENDED = timedelta(minutes=10)
MAX_ATTEMPTS_PER_TICK = 500
CONCURRENCY = 8


@dataclass
class ExitReport:
    checked: int = 0
    exited: int = 0
    failed: dict[str, int] = field(default_factory=dict)
    errors: int = 0


async def tick(
    sessions: async_sessionmaker[AsyncSession], runtime: RuntimeAdapter, now: datetime
) -> ExitReport:
    report = ExitReport()
    async with sessions() as session:
        rows = (
            await session.execute(
                select(RunAttempt.run_id, RunAttempt.attempt, RunAttempt.runtime_ref)
                .join(AgentRun, AgentRun.id == RunAttempt.run_id)
                .where(
                    RunAttempt.runtime_ref.is_not(None),
                    RunAttempt.exit_code.is_(None),
                    or_(
                        and_(
                            AgentRun.state.in_([s.value for s in WATCHED_STATES]),
                            RunAttempt.attempt == AgentRun.current_attempt,
                        ),
                        RunAttempt.ended_at > now - RECENTLY_ENDED,
                    ),
                )
                .order_by(RunAttempt.started_at)
                .limit(MAX_ATTEMPTS_PER_TICK)
            )
        ).all()
    if not rows:
        return report

    limit = asyncio.Semaphore(CONCURRENCY)

    async def status(ref: str) -> RuntimeStatus | None:
        async with limit:
            try:
                return await runtime.get_run(ref)
            except Exception:  # the daemon is restarting or unreachable: try next tick
                report.errors += 1
                return None

    refs = [str(row.runtime_ref) for row in rows]
    statuses = await asyncio.gather(*(status(ref) for ref in refs))
    report.checked = len(rows)
    for row, ref, observed in zip(rows, refs, statuses, strict=True):
        if observed is None or observed.state != "exited":
            continue
        report.exited += 1
        code = await _record(sessions, row.run_id, row.attempt, ref, observed)
        if code:
            report.failed[code] = report.failed.get(code, 0) + 1
            log.info(
                "agent container exited: %s",
                code,
                extra={
                    "event": "run.container_exited",
                    "run_id": str(row.run_id),
                    "attempt": row.attempt,
                    "exit_code": observed.exit_code,
                    "oom_killed": observed.oom_killed,
                },
            )
    if report.errors:
        log.warning(
            "runtime status unavailable for %d attempt(s)",
            report.errors,
            extra={"event": "exit_watch.runtime_error"},
        )
    return report


async def _record(
    sessions: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    attempt: int,
    ref: str,
    observed: RuntimeStatus,
) -> str | None:
    async with sessions() as session, session.begin():
        return await service.record_container_exit(
            session,
            run_id,
            attempt,
            ref,
            exit_code=observed.exit_code,
            oom_killed=observed.oom_killed,
            memory_limit_bytes=observed.memory_limit_bytes,
        )
