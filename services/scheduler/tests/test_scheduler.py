from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, text

from conftest import install_hello
from crewquarters_scheduler import scheduler
from crewquarters_scheduler.worker import Worker
from crewquarters_shared import jobs
from crewquarters_shared.db.models import AgentRun, AuditEvent, Job, Schedule
from crewquarters_shared.fakes.runtime import FakeRuntime

pytestmark = pytest.mark.usefixtures("catalog_synced")


async def make_schedule(
    owner: httpx.AsyncClient, cron: str, timezone: str, policy: str = "fire_once"
) -> dict:
    installation = await install_hello(owner)
    response = await owner.post(
        "/api/v1/schedules",
        json={
            "installationId": installation["id"],
            "cron": cron,
            "timezone": timezone,
            "misfirePolicy": policy,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def set_next_run(sessions, schedule_id: str, at: datetime) -> None:
    async with sessions() as db, db.begin():
        await db.execute(
            text("UPDATE schedules SET next_run_at = :at WHERE id = :id"),
            {"at": at, "id": schedule_id},
        )


async def count_runs(sessions) -> int:
    async with sessions() as db:
        return int(await db.scalar(select(func.count()).select_from(AgentRun)))


@pytest.mark.parametrize("timezone", ["Asia/Kolkata", "America/New_York", "Europe/London"])
async def test_fires_exactly_at_10_local(owner: httpx.AsyncClient, sessions, timezone: str) -> None:
    schedule = await make_schedule(owner, "0 10 * * *", timezone)
    due = datetime.fromisoformat(schedule["nextRunAt"])
    local = due.astimezone(__import__("zoneinfo").ZoneInfo(timezone))
    assert (local.hour, local.minute) == (10, 0)

    async with sessions() as db, db.begin():
        assert await scheduler.tick(db, due - timedelta(seconds=1), 60) == []
    async with sessions() as db, db.begin():
        fired = await scheduler.tick(db, due + timedelta(seconds=1), 60)
    assert [f.outcome for f in fired] == ["fired"]
    assert fired[0].scheduled_for == due
    async with sessions() as db:
        run = await db.scalar(select(AgentRun))
        assert run.trigger == "schedule" and run.scheduled_for == due
        nxt = await db.scalar(select(Schedule.next_run_at))
        assert nxt == due + timedelta(days=1) or abs((nxt - due).total_seconds() - 86400) <= 3600


async def test_duplicate_ticks_create_one_run(owner: httpx.AsyncClient, sessions) -> None:
    schedule = await make_schedule(owner, "*/5 * * * *", "UTC")
    due = datetime.fromisoformat(schedule["nextRunAt"])

    async def one_tick() -> None:
        async with sessions() as db, db.begin():
            await scheduler.tick(db, due + timedelta(seconds=1), 60)

    await asyncio.gather(*(one_tick() for _ in range(5)))
    assert await count_runs(sessions) == 1

    # Even if next_run_at is rewound (e.g. a restored backup), the unique index holds.
    await set_next_run(sessions, schedule["id"], due)
    async with sessions() as db, db.begin():
        fired = await scheduler.tick(db, due + timedelta(seconds=2), 60)
    assert [f.outcome for f in fired] == ["duplicate"]
    assert await count_runs(sessions) == 1
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Job)) == 1


async def test_fire_once_after_downtime(owner: httpx.AsyncClient, sessions) -> None:
    schedule = await make_schedule(owner, "0 * * * *", "UTC")
    now = datetime(2026, 9, 24, 15, 20, tzinfo=UTC)
    await set_next_run(sessions, schedule["id"], datetime(2026, 9, 24, 10, 0, tzinfo=UTC))
    async with sessions() as db, db.begin():
        fired = await scheduler.tick(db, now, 60)
    assert len(fired) == 1
    assert fired[0].scheduled_for == datetime(2026, 9, 24, 15, 0, tzinfo=UTC)
    assert await count_runs(sessions) == 1
    async with sessions() as db:
        assert await db.scalar(select(Schedule.next_run_at)) == datetime(
            2026, 9, 24, 16, 0, tzinfo=UTC
        )
        actions = (await db.scalars(select(AuditEvent.action))).all()
        assert "schedule.misfire_fired_once" in actions


async def test_skip_policy_after_downtime(owner: httpx.AsyncClient, sessions) -> None:
    schedule = await make_schedule(owner, "0 * * * *", "UTC", policy="skip")
    now = datetime(2026, 9, 24, 15, 20, tzinfo=UTC)
    await set_next_run(sessions, schedule["id"], datetime(2026, 9, 24, 10, 0, tzinfo=UTC))
    async with sessions() as db, db.begin():
        fired = await scheduler.tick(db, now, 60)
    assert [f.outcome for f in fired] == ["misfire_skipped"]
    assert await count_runs(sessions) == 0


async def test_disabled_installation_skips_schedule(owner: httpx.AsyncClient, sessions) -> None:
    schedule = await make_schedule(owner, "0 * * * *", "UTC")
    inst = (await owner.get(f"/api/v1/agent-installations/{schedule['installationId']}")).json()
    await owner.patch(
        f"/api/v1/agent-installations/{inst['id']}",
        json={"version": inst["version"], "enabled": False},
    )
    due = datetime.fromisoformat(schedule["nextRunAt"])
    async with sessions() as db, db.begin():
        fired = await scheduler.tick(db, due + timedelta(seconds=1), 60)
    assert [f.outcome for f in fired] == ["installation_unavailable"]
    assert await count_runs(sessions) == 0


async def test_worker_crash_after_claim_recovers_without_duplicate_run(
    owner: httpx.AsyncClient, sessions, settings
) -> None:
    """Gate S2: kill a worker between claim and completion; the job is recovered and
    exactly one run record and one container exist."""
    schedule = await make_schedule(owner, "*/5 * * * *", "UTC")
    due = datetime.fromisoformat(schedule["nextRunAt"])
    async with sessions() as db, db.begin():
        await scheduler.tick(db, due + timedelta(seconds=1), 60)

    runtime = FakeRuntime(
        sessions, heartbeat_seconds=settings.heartbeat_timeout_seconds, step_seconds=0.05
    )
    crashed = Worker(sessions, runtime, settings, worker_id="crashed")
    claimed = await crashed.claim()  # ...and the process dies before executing it
    assert claimed is not None and claimed.type == "run.dispatch"

    async with sessions() as db, db.begin():
        await db.execute(text("UPDATE jobs SET lease_until = now() - interval '1 second'"))
        requeued, _ = await jobs.reap_expired(db)
    assert requeued == [claimed.id]

    survivor = Worker(sessions, runtime, settings, worker_id="survivor")
    assert await survivor.run_once() is True
    for _ in range(100):
        async with sessions() as db:
            state = await db.scalar(select(AgentRun.state))
        if state == "SUCCEEDED":
            break
        await asyncio.sleep(0.05)
    assert state == "SUCCEEDED"
    assert await count_runs(sessions) == 1
    assert len(runtime.started) == 1
    await runtime.close()


async def test_worker_crash_after_container_start_does_not_start_twice(
    owner: httpx.AsyncClient, sessions, settings
) -> None:
    installation = await install_hello(owner, {"fakeScenario": "slow"})
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    runtime = FakeRuntime(
        sessions, heartbeat_seconds=settings.heartbeat_timeout_seconds, step_seconds=0.05
    )
    first = Worker(sessions, runtime, settings, worker_id="first")
    job = await first.claim()
    await first.handle_dispatch(job.payload)  # container started, but job never completed
    async with sessions() as db, db.begin():
        await db.execute(text("UPDATE jobs SET lease_until = now() - interval '1 second'"))
        await jobs.reap_expired(db)
    second = Worker(sessions, runtime, settings, worker_id="second")
    assert await second.run_once() is True
    assert runtime.started == [f"fake-{run['id']}-1"]
    await runtime.close()
