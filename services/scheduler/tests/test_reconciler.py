from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, text

from conftest import install_hello, wait_for_state
from crewquarters_scheduler import reconciler
from crewquarters_shared.db.models import AgentRun
from crewquarters_shared.runs import service
from crewquarters_shared.timeutil import utcnow

pytestmark = pytest.mark.usefixtures("catalog_synced")


async def test_active_clock_pauses_while_waiting_for_input(
    owner: httpx.AsyncClient, sessions, platform
) -> None:
    installation = await install_hello(owner, {"fakeScenario": "ask"})
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    await wait_for_state(owner, run["id"], {"WAITING_INPUT"})
    async with sessions() as db:
        row = await db.get(AgentRun, run["id"])
    # 590s of waiting would exceed the 600s active limit if waiting counted.
    later = utcnow() + timedelta(seconds=590)
    assert service.active_seconds(row, later) < 60
    assert service.wait_seconds(row, later) >= 589
    async with sessions() as db, db.begin():
        report = await reconciler.tick(db, later)
    assert report.active_timeouts == 0 and report.input_timeouts == 0


async def test_input_wait_limit_fails_run(owner: httpx.AsyncClient, sessions, platform) -> None:
    installation = await install_hello(owner, {"fakeScenario": "ask"})
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    await wait_for_state(owner, run["id"], {"WAITING_INPUT"})
    platform.stop.set()  # freeze background loops so the test controls time
    async with sessions() as db, db.begin():
        report = await reconciler.tick(db, utcnow() + timedelta(seconds=3601))
    assert report.input_timeouts == 1
    final = (await owner.get(f"/api/v1/runs/{run['id']}")).json()
    assert final["state"] == "FAILED" and final["error"]["code"] == "INPUT_TIMEOUT"
    pending = (await owner.get("/api/v1/input-requests", params={"state": "cancelled"})).json()[
        "items"
    ]
    assert len(pending) == 1


async def test_active_timeout_fails_run(owner: httpx.AsyncClient, sessions, platform) -> None:
    installation = await install_hello(owner, {"fakeScenario": "slow"})
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    await wait_for_state(owner, run["id"], {"RUNNING"})
    platform.stop.set()
    async with sessions() as db, db.begin():
        report = await reconciler.tick(db, utcnow() + timedelta(seconds=601))
    assert report.active_timeouts == 1
    final = (await owner.get(f"/api/v1/runs/{run['id']}")).json()
    assert final["state"] == "FAILED" and final["error"]["code"] == "ACTIVE_TIMEOUT"
    assert final["retryable"] is False
    async with sessions() as db:
        stop_jobs = await db.scalar(text("SELECT count(*) FROM jobs WHERE type = 'run.stop'"))
    assert stop_jobs == 1


async def test_expired_input_resumes_run(owner: httpx.AsyncClient, sessions, platform) -> None:
    installation = await install_hello(owner, {"fakeScenario": "ask"})
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    await wait_for_state(owner, run["id"], {"WAITING_INPUT"})
    async with sessions() as db, db.begin():
        await db.execute(text("UPDATE input_requests SET deadline = now() - interval '1 second'"))
    final = await wait_for_state(owner, run["id"], {"SUCCEEDED"})
    assert final["result"]["decision"] is None
    async with sessions() as db:
        assert (
            await db.scalar(select(text("state")).select_from(text("input_requests"))) == "expired"
        )
