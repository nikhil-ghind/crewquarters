"""PostgreSQL job queue (PLAN.md section 6.2).

* ``enqueue`` is idempotent on ``dedupe_key`` among live (available/claimed) jobs.
* ``claim`` uses ``FOR UPDATE SKIP LOCKED``, sets a lease, and increments attempts.
  Callers commit before executing so the lease is visible to other workers.
* ``heartbeat`` extends a lease only for the current owner.
* ``reap_expired`` returns expired leases to ``available`` or moves them to ``dead``.
* ``fail`` retries with bounded exponential backoff plus jitter, or marks ``dead``.

This module is the reviewed access module for the shared ``jobs`` table.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_shared.timeutil import utcnow

BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 300.0


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    type: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    lease_owner: str
    lease_until: datetime


def backoff_seconds(attempts: int, rng: random.Random | None = None) -> float:
    """Bounded exponential backoff with full jitter in the upper half."""
    rng = rng or random.Random()  # noqa: S311 - jitter, not security
    ceiling = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)))
    return float(ceiling / 2 + rng.random() * ceiling / 2)


async def enqueue(
    session: AsyncSession,
    job_type: str,
    payload: dict[str, Any] | None = None,
    *,
    dedupe_key: str | None = None,
    available_at: datetime | None = None,
    max_attempts: int = 5,
) -> int | None:
    """Insert a job; return its id, or ``None`` if a live job with the key already exists."""
    row = await session.execute(
        text(
            """
            INSERT INTO jobs
                (type, payload, state, available_at, attempts, max_attempts, dedupe_key)
            VALUES (:type, CAST(:payload AS jsonb), 'available', :available_at, 0,
                    :max_attempts, :dedupe_key)
            ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL
                AND state IN ('available', 'claimed')
            DO NOTHING
            RETURNING id
            """
        ),
        {
            "type": job_type,
            "payload": _json(payload or {}),
            "available_at": available_at or utcnow(),
            "max_attempts": max_attempts,
            "dedupe_key": dedupe_key,
        },
    )
    return row.scalar_one_or_none()


async def claim(
    session: AsyncSession,
    worker_id: str,
    lease_seconds: int,
    types: list[str] | None = None,
) -> ClaimedJob | None:
    type_filter = "AND type = ANY(:types)" if types else ""
    result = await session.execute(
        text(
            f"""
            UPDATE jobs SET state = 'claimed', lease_owner = :owner,
                lease_until = now() + make_interval(secs => :lease), attempts = attempts + 1,
                updated_at = now()
            WHERE id = (
                SELECT id FROM jobs
                WHERE state = 'available' AND available_at <= now() {type_filter}
                ORDER BY available_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, type, payload, attempts, max_attempts, lease_owner, lease_until
            """  # noqa: S608 - type_filter is a constant fragment
        ),
        {"owner": worker_id, "lease": lease_seconds, "types": types},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return ClaimedJob(**row)


async def heartbeat(session: AsyncSession, job_id: int, worker_id: str, lease_seconds: int) -> bool:
    result = await session.execute(
        text(
            """
            UPDATE jobs SET lease_until = now() + make_interval(secs => :lease), updated_at = now()
            WHERE id = :id AND state = 'claimed' AND lease_owner = :owner
            """
        ),
        {"id": job_id, "owner": worker_id, "lease": lease_seconds},
    )
    return (result.rowcount or 0) == 1  # type: ignore[attr-defined]


async def complete(session: AsyncSession, job_id: int, worker_id: str) -> bool:
    result = await session.execute(
        text(
            """
            UPDATE jobs SET state = 'succeeded', finished_at = now(), lease_owner = NULL,
                lease_until = NULL, updated_at = now()
            WHERE id = :id AND state = 'claimed' AND lease_owner = :owner
            """
        ),
        {"id": job_id, "owner": worker_id},
    )
    return (result.rowcount or 0) == 1  # type: ignore[attr-defined]


async def fail(
    session: AsyncSession,
    job_id: int,
    worker_id: str,
    error: dict[str, Any],
    *,
    retryable: bool,
) -> str | None:
    """Record a failure. Return the new state (``available`` or ``dead``), or ``None``
    if this worker no longer owns the lease."""
    row = (
        await session.execute(
            text(
                "SELECT attempts, max_attempts FROM jobs "
                "WHERE id = :id AND state = 'claimed' AND lease_owner = :owner FOR UPDATE"
            ),
            {"id": job_id, "owner": worker_id},
        )
    ).one_or_none()
    if row is None:
        return None
    attempts, max_attempts = row
    if retryable and attempts < max_attempts:
        delay = backoff_seconds(attempts)
        await session.execute(
            text(
                """
                UPDATE jobs SET state = 'available', lease_owner = NULL, lease_until = NULL,
                    available_at = now() + make_interval(secs => :delay),
                    last_error = CAST(:error AS jsonb), updated_at = now()
                WHERE id = :id
                """
            ),
            {"id": job_id, "delay": delay, "error": _json(error)},
        )
        return "available"
    await session.execute(
        text(
            """
            UPDATE jobs SET state = 'dead', lease_owner = NULL, lease_until = NULL,
                finished_at = now(), last_error = CAST(:error AS jsonb), updated_at = now()
            WHERE id = :id
            """
        ),
        {"id": job_id, "error": _json(error)},
    )
    return "dead"


async def reap_expired(session: AsyncSession) -> tuple[list[int], list[dict[str, Any]]]:
    """Return expired leases to the queue. Returns (requeued ids, jobs moved to dead)."""
    requeued = await session.execute(
        text(
            """
            UPDATE jobs SET state = 'available', lease_owner = NULL, lease_until = NULL,
                last_error = '{"code": "LEASE_EXPIRED"}'::jsonb, updated_at = now()
            WHERE state = 'claimed' AND lease_until < now() AND attempts < max_attempts
            RETURNING id
            """
        )
    )
    dead = await session.execute(
        text(
            """
            UPDATE jobs SET state = 'dead', lease_owner = NULL, lease_until = NULL,
                finished_at = now(), last_error = '{"code": "LEASE_EXPIRED"}'::jsonb,
                updated_at = now()
            WHERE state = 'claimed' AND lease_until < now() AND attempts >= max_attempts
            RETURNING id, type, payload
            """
        )
    )
    return [r[0] for r in requeued], [dict(r) for r in dead.mappings()]


async def cancel_by_dedupe(session: AsyncSession, dedupe_key: str) -> int:
    result = await session.execute(
        text(
            """
            UPDATE jobs SET state = 'cancelled', finished_at = now(), updated_at = now()
            WHERE dedupe_key = :key AND state = 'available'
            """
        ),
        {"key": dedupe_key},
    )
    return result.rowcount or 0  # type: ignore[attr-defined]


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


def lease_deadline(seconds: int) -> datetime:
    return utcnow() + timedelta(seconds=seconds)
