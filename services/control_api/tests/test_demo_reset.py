"""Demo reset for the real platform (PLAN.md section 24, closing paragraph)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text

from conftest import install_hello, wait_for_state
from crewquarters_api.demo import ResetBlocked, reset
from crewquarters_shared.config import Settings
from crewquarters_shared.db.models import AgentInstallation, AgentRun, AuditEvent, Schedule
from crewquarters_shared.db.models_gateway import ChatMessage, ChatSession, ModelCatalogEntry
from crewquarters_shared.timeutil import utcnow

pytestmark = pytest.mark.usefixtures("catalog_synced")


async def _chat(sessions: Any, user_id: uuid.UUID, enabled: bool) -> uuid.UUID:
    async with sessions() as db:
        if await db.get(ModelCatalogEntry, "local.general.small") is None:
            db.add(
                ModelCatalogEntry(
                    id="local.general.small",
                    family="local.general",
                    display_name="General (small)",
                    backend="mock",
                    profile={},
                    expected_memory_bytes=1,
                    context_limit=8192,
                    capabilities=["chat"],
                )
            )
            await db.flush()
        chat = ChatSession(
            user_id=user_id, title="Rehearsal", model_profile="local.general.small", enabled=enabled
        )
        db.add(chat)
        await db.flush()
        db.add(ChatMessage(session_id=chat.id, role="user", content="hello", citations=[]))
        await db.commit()
        return chat.id


async def _count(sessions: Any, model: Any) -> int:
    async with sessions() as db:
        return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def test_reset_cancels_deletes_and_keeps_setup(
    owner: httpx.AsyncClient, platform: Any, sessions: Any, settings: Settings
) -> None:
    installation = await install_hello(owner)
    done = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    await wait_for_state(owner, done["id"], {"SUCCEEDED"})
    waiting_install = await install_hello(owner, {"fakeScenario": "ask"})
    waiting = (
        await owner.post("/api/v1/runs", json={"installationId": waiting_install["id"]})
    ).json()
    await wait_for_state(owner, waiting["id"], {"WAITING_INPUT"})
    schedule = await owner.post(
        "/api/v1/schedules",
        json={"installationId": installation["id"], "cron": "0 10 * * *", "timezone": "UTC"},
    )
    assert schedule.status_code == 201, schedule.text
    me = (await owner.get("/api/v1/me")).json()["user"]["id"]
    enabled_chat = await _chat(sessions, uuid.UUID(me), enabled=True)
    await _chat(sessions, uuid.UUID(me), enabled=False)
    released: list[str] = []

    async def release(session_id: str) -> None:
        released.append(session_id)

    report = await reset(
        sessions, settings, cancel_timeout=15, release_lease=release, say=lambda _: None
    )
    assert report.cancelled == 1
    assert report.runs_deleted == 2
    assert report.chat_sessions_deleted == 2 and released == [str(enabled_chat)]
    assert report.schedules_rescheduled == 1 and report.schedules_deleted == 0

    assert await _count(sessions, AgentRun) == 0
    assert await _count(sessions, ChatSession) == 0
    assert await _count(sessions, ChatMessage) == 0
    async with sessions() as db:
        for table in ("run_events", "run_attempts", "input_requests"):
            assert await db.scalar(text(f"SELECT count(*) FROM {table}")) == 0  # noqa: S608
        assert await db.scalar(text("SELECT count(*) FROM jobs WHERE type LIKE 'run.%'")) == 0
        kept_schedule = (await db.scalars(select(Schedule))).one()
        assert kept_schedule.next_run_at is not None
        assert kept_schedule.next_run_at > utcnow() - timedelta(seconds=5)
        actions = (await db.scalars(select(AuditEvent.action))).all()
    assert await _count(sessions, AgentInstallation) == 2  # installations kept
    assert "demo.reset" in actions
    # The platform still works after the reset.
    again = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    await wait_for_state(owner, again["id"], {"SUCCEEDED"})


async def test_reset_since_and_schedule_flag(
    owner: httpx.AsyncClient, platform: Any, sessions: Any, settings: Settings
) -> None:
    installation = await install_hello(owner)
    old = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    await wait_for_state(owner, old["id"], {"SUCCEEDED"})
    async with sessions() as db:
        await db.execute(
            text("UPDATE agent_runs SET created_at = now() - interval '2 days' WHERE id = :id"),
            {"id": old["id"]},
        )
        await db.commit()
    new = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    await wait_for_state(owner, new["id"], {"SUCCEEDED"})
    await owner.post(
        "/api/v1/schedules",
        json={"installationId": installation["id"], "cron": "0 10 * * *", "timezone": "UTC"},
    )
    report = await reset(
        sessions,
        settings,
        since=utcnow() - timedelta(hours=1),
        include_schedules=True,
        say=lambda _: None,
    )
    assert report.runs_deleted == 1 and report.schedules_deleted == 1
    remaining = (await owner.get("/api/v1/runs")).json()["items"]
    assert [r["id"] for r in remaining] == [old["id"]]
    assert (await owner.get("/api/v1/schedules")).json()["items"] == []


async def test_reset_refuses_when_runs_do_not_stop(
    owner: httpx.AsyncClient, sessions: Any, settings: Settings
) -> None:
    # No worker is running, so a started run cannot finish cancelling.
    installation = await install_hello(owner)
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    async with sessions() as db:
        await db.execute(
            text("UPDATE agent_runs SET state = 'RUNNING' WHERE id = :id"), {"id": run["id"]}
        )
        await db.commit()
    with pytest.raises(ResetBlocked):
        await reset(sessions, settings, cancel_timeout=0.5, say=lambda _: None)
    assert await _count(sessions, AgentRun) == 1  # nothing deleted
    report = await reset(sessions, settings, cancel_timeout=0.5, force=True, say=lambda _: None)
    assert report.runs_deleted == 1 and report.warnings
