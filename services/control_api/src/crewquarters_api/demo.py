"""Demo reset for the real platform (``cq-admin demo reset``; PLAN.md section 24).

Between rehearsals: cancel active runs and wait for them to stop, release chat model
leases, then delete what a rehearsal created: runs (with their attempts, events, input
requests, telephony calls and action claims, which cascade) and chat sessions (with their
messages), plus finished run jobs. Users, installations, schedules, connections, models,
knowledge bases and the audit log are kept; ``--schedules`` and ``--knowledge`` also clear
those. Reseeding re-syncs the bundled agent catalog and moves every schedule's next run
to its next occurrence after now, so the reset does not trigger misfire runs.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_api import catalog
from crewquarters_shared import audit
from crewquarters_shared.config import Settings
from crewquarters_shared.cron import next_occurrence
from crewquarters_shared.db.models import AgentRun, Schedule
from crewquarters_shared.db.models_gateway import ChatSession
from crewquarters_shared.runs import service
from crewquarters_shared.runs.states import CANCELLABLE_STATES, TERMINAL_STATES
from crewquarters_shared.timeutil import utcnow

log = logging.getLogger(__name__)

ReleaseLease = Callable[[str], Awaitable[None]]
DeleteKnowledgeBase = Callable[[str], Awaitable[None]]


class ResetBlocked(Exception):
    """Runs did not stop within the timeout; nothing was deleted."""


@dataclass
class ResetReport:
    cancelled: int = 0
    runs_deleted: int = 0
    chat_sessions_deleted: int = 0
    leases_released: int = 0
    jobs_deleted: int = 0
    schedules_deleted: int = 0
    knowledge_bases_deleted: int = 0
    schedules_rescheduled: int = 0
    catalog: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _since(stmt: Any, column: Any, since: datetime | None) -> Any:
    return stmt.where(column >= since) if since is not None else stmt


async def _cancel_active(sessions: async_sessionmaker[AsyncSession], since: datetime | None) -> int:
    cancelled = 0
    async with sessions() as db, db.begin():
        ids = (
            await db.scalars(
                _since(
                    select(AgentRun.id).where(AgentRun.state.in_(CANCELLABLE_STATES)),
                    AgentRun.created_at,
                    since,
                )
            )
        ).all()
        for run_id in ids:
            run = await service.lock_run(db, run_id)
            if run.state in CANCELLABLE_STATES:
                await service.request_cancel(db, run, reason="demo reset")
                cancelled += 1
    return cancelled


async def _wait_stopped(
    sessions: async_sessionmaker[AsyncSession], since: datetime | None, wait_seconds: float
) -> int:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_seconds
    while True:
        async with sessions() as db:
            active = await db.scalar(
                _since(
                    select(func.count())
                    .select_from(AgentRun)
                    .where(AgentRun.state.not_in(TERMINAL_STATES)),
                    AgentRun.created_at,
                    since,
                )
            )
        if not active or loop.time() >= deadline:
            return int(active or 0)
        await asyncio.sleep(0.5)


async def reset(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    since: datetime | None = None,
    include_schedules: bool = False,
    include_knowledge: bool = False,
    cancel_timeout: float = 60.0,
    force: bool = False,
    release_lease: ReleaseLease | None = None,
    delete_knowledge_base: DeleteKnowledgeBase | None = None,
    say: Callable[[str], None] = print,
) -> ResetReport:
    report = ResetReport()
    report.cancelled = await _cancel_active(sessions, since)
    if report.cancelled:
        say(f"Cancelling {report.cancelled} active run(s)...")
    still_active = await _wait_stopped(sessions, since, cancel_timeout)
    if still_active and not force:
        raise ResetBlocked(
            f"{still_active} run(s) are still stopping after {cancel_timeout:.0f}s. Is the "
            "scheduler running? Retry, or pass --force to delete them anyway."
        )
    if still_active:
        report.warnings.append(f"{still_active} run(s) were deleted before they stopped.")

    # Chat: release leases first (the gateway owns them), then delete sessions.
    async with sessions() as db:
        chats = (await db.scalars(_since(select(ChatSession), ChatSession.created_at, since))).all()
    for chat in chats:
        if chat.enabled and release_lease is not None:
            try:
                await release_lease(str(chat.id))
                report.leases_released += 1
            except Exception as exc:
                report.warnings.append(
                    f"could not release the model lease of chat {chat.id}: {type(exc).__name__}"
                )
    async with sessions() as db, db.begin():
        result = await db.execute(
            _since(delete(ChatSession), ChatSession.created_at, since).returning(ChatSession.id)
        )
        report.chat_sessions_deleted = len(result.all())
        # Cascades: run_attempts, run_events, input_requests, telephony_calls,
        # idempotency_actions.
        result = await db.execute(
            _since(delete(AgentRun), AgentRun.created_at, since).returning(AgentRun.id)
        )
        deleted_runs = [str(r[0]) for r in result.all()]
        report.runs_deleted = len(deleted_runs)
        if deleted_runs:
            jobs = await db.execute(
                text(
                    "DELETE FROM jobs WHERE type LIKE 'run.%' "
                    "AND payload->>'runId' = ANY(:ids) RETURNING id"
                ),
                {"ids": deleted_runs},
            )
            report.jobs_deleted = len(jobs.all())
        if include_schedules:
            gone = await db.execute(
                text(
                    "UPDATE schedules SET deleted_at = now(), enabled = false, "
                    "version = version + 1 WHERE deleted_at IS NULL RETURNING id"
                )
            )
            report.schedules_deleted = len(gone.all())

    if include_knowledge:
        async with sessions() as db:
            kbs = (await db.execute(text("SELECT id FROM knowledge_bases"))).scalars().all()
        for kb_id in kbs:
            if delete_knowledge_base is None:
                report.warnings.append("knowledge bases kept: the knowledge service is not set")
                break
            try:
                await delete_knowledge_base(str(kb_id))
                report.knowledge_bases_deleted += 1
            except Exception as exc:
                report.warnings.append(
                    f"could not delete knowledge base {kb_id}: {type(exc).__name__}"
                )

    # Reseed.
    if settings.catalog_dir:
        async with sessions() as db:
            report.catalog = await catalog.sync_directory(db, settings.catalog_dir)
            await db.commit()
    now = utcnow()
    async with sessions() as db, db.begin():
        schedules = (
            await db.scalars(
                select(Schedule).where(Schedule.deleted_at.is_(None), Schedule.enabled)
            )
        ).all()
        for schedule in schedules:
            schedule.next_run_at = next_occurrence(schedule.cron, schedule.timezone, now)
            schedule.last_run_id = None
            report.schedules_rescheduled += 1
        audit.record(
            db,
            action="demo.reset",
            actor_type="system",
            metadata={
                "since": since.isoformat() if since else None,
                "runsDeleted": report.runs_deleted,
                "chatSessionsDeleted": report.chat_sessions_deleted,
                "schedulesDeleted": report.schedules_deleted,
                "knowledgeBasesDeleted": report.knowledge_bases_deleted,
            },
        )
    return report
