from __future__ import annotations

import asyncio
import random

from sqlalchemy import text

from crewquarters_shared import jobs


async def test_enqueue_dedupes_live_jobs(sessions) -> None:
    async with sessions() as db, db.begin():
        first = await jobs.enqueue(db, "t", {"n": 1}, dedupe_key="k")
        second = await jobs.enqueue(db, "t", {"n": 2}, dedupe_key="k")
    assert first is not None and second is None
    async with sessions() as db, db.begin():
        job = await jobs.claim(db, "w", 30)
        assert job is not None
        await jobs.complete(db, job.id, "w")
    async with sessions() as db, db.begin():
        # A finished job no longer blocks the key.
        assert await jobs.enqueue(db, "t", {"n": 3}, dedupe_key="k") is not None


async def test_concurrent_claims_never_share_a_job(sessions) -> None:
    async with sessions() as db, db.begin():
        for i in range(20):
            await jobs.enqueue(db, "t", {"n": i})

    async def claimer(name: str) -> list[int]:
        got = []
        while True:
            async with sessions() as db, db.begin():
                job = await jobs.claim(db, name, 30)
            if job is None:
                return got
            got.append(job.id)

    results = await asyncio.gather(*(claimer(f"w{i}") for i in range(5)))
    claimed = [j for r in results for j in r]
    assert len(claimed) == 20 and len(set(claimed)) == 20


async def test_expired_lease_is_recovered_then_dead_after_max_attempts(sessions) -> None:
    async with sessions() as db, db.begin():
        await jobs.enqueue(db, "t", {}, max_attempts=2)
    for attempt in (1, 2):
        async with sessions() as db, db.begin():
            job = await jobs.claim(db, "crashy", 30)
            assert job is not None and job.attempts == attempt
            await db.execute(text("UPDATE jobs SET lease_until = now() - interval '1 second'"))
        async with sessions() as db, db.begin():
            requeued, dead = await jobs.reap_expired(db)
        if attempt == 1:
            assert requeued == [job.id] and dead == []
        else:
            assert requeued == [] and [d["id"] for d in dead] == [job.id]
    async with sessions() as db:
        assert await db.scalar(text("SELECT state FROM jobs")) == "dead"


async def test_heartbeat_and_completion_require_lease_owner(sessions) -> None:
    async with sessions() as db, db.begin():
        await jobs.enqueue(db, "t", {})
        job = await jobs.claim(db, "owner", 30)
    async with sessions() as db, db.begin():
        assert await jobs.heartbeat(db, job.id, "intruder", 30) is False
        assert await jobs.complete(db, job.id, "intruder") is False
        assert await jobs.heartbeat(db, job.id, "owner", 30) is True
        assert await jobs.complete(db, job.id, "owner") is True


async def test_retryable_failure_backs_off(sessions) -> None:
    async with sessions() as db, db.begin():
        await jobs.enqueue(db, "t", {}, max_attempts=3)
        job = await jobs.claim(db, "w", 30)
    async with sessions() as db, db.begin():
        assert await jobs.fail(db, job.id, "w", {"code": "X"}, retryable=True) == "available"
    async with sessions() as db, db.begin():
        assert await jobs.claim(db, "w", 30) is None  # not yet available
        delay = await db.scalar(text("SELECT extract(epoch FROM available_at - now()) FROM jobs"))
        assert 0.5 < delay <= 2.1
    async with sessions() as db, db.begin():
        await db.execute(text("UPDATE jobs SET available_at = now()"))
        job = await jobs.claim(db, "w", 30)
    async with sessions() as db, db.begin():
        assert await jobs.fail(db, job.id, "w", {"code": "X"}, retryable=False) == "dead"


def test_backoff_is_bounded() -> None:
    rng = random.Random(1)
    values = [jobs.backoff_seconds(n, rng) for n in range(1, 20)]
    assert all(v <= jobs.BACKOFF_MAX_SECONDS for v in values)
    assert values[0] <= 2 and values[-1] >= jobs.BACKOFF_MAX_SECONDS / 2
