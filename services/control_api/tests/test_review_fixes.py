"""Regression tests for issues found in the control-plane code review."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import text

from conftest import install_hello
from crewquarters_scheduler import reconciler
from crewquarters_scheduler.worker import Worker, token_ttl_seconds
from crewquarters_shared.runs import service
from crewquarters_shared.timeutil import utcnow

pytestmark = pytest.mark.usefixtures("catalog_synced")

SERVICE = {"Authorization": "Bearer dev-insecure-internal-token-change-me-0000"}
ASK = {
    "key": "confirm",
    "title": "Go?",
    "prompt": "Proceed?",
    "schema": {"type": "boolean"},
    "timeoutSeconds": 600,
}


class InertRuntime:
    """Starts nothing; the test plays the SDK. Records whether the run row was locked."""

    def __init__(self, sessions=None) -> None:
        self.sessions = sessions
        self.lock_was_free: list[bool] = []
        self.stopped: list[str] = []

    async def start_run(self, spec):
        if self.sessions is not None:
            async with self.sessions() as db, db.begin():
                self.lock_was_free.append(await service.try_lock_run(db, spec.run_id) is not None)
        return f"inert-{spec.run_id}-{spec.attempt}"

    async def stop_run(self, ref, grace_seconds):
        self.stopped.append(ref)


async def dispatch(sessions, settings, runtime=None) -> None:
    """Drain the job queue (dispatch plus any stop/cancel jobs)."""
    worker = Worker(sessions, runtime or InertRuntime(), settings, worker_id="t")
    ran = 0
    while await worker.run_once():
        ran += 1
    assert ran


async def new_run(owner: httpx.AsyncClient, sessions, settings, config=None) -> str:
    installation = await install_hello(owner, config)
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    await dispatch(sessions, settings)
    return str(run["id"])


async def test_retry_after_restart_reopens_the_same_question(owner, sessions, settings) -> None:
    run_id = await new_run(owner, sessions, settings)
    base = f"/internal/v1/runs/{run_id}"
    await owner.post(f"{base}/handshake", json={"attempt": 1}, headers=SERVICE)
    first = (
        await owner.post(f"{base}/input-requests", json={"attempt": 1, **ASK}, headers=SERVICE)
    ).json()
    async with sessions() as db, db.begin():  # the host restarts: heartbeats stop
        await db.execute(
            text("UPDATE run_attempts SET heartbeat_expires_at = now() - interval '1 s'")
        )
    async with sessions() as db, db.begin():
        assert (await reconciler.tick(db, utcnow())).interrupted == 1
    assert (await owner.get(f"/api/v1/runs/{run_id}")).json()["state"] == "INTERRUPTED"

    retried = (await owner.post(f"/api/v1/runs/{run_id}/retry")).json()
    assert retried["inputWaitSecondsUsed"] == 0  # time limits are per attempt
    await dispatch(sessions, settings)
    await owner.post(f"{base}/handshake", json={"attempt": 2}, headers=SERVICE)
    again = (
        await owner.post(f"{base}/input-requests", json={"attempt": 2, **ASK}, headers=SERVICE)
    ).json()
    assert again["id"] == first["id"] and again["state"] == "pending"
    assert again["version"] > first["version"]
    assert (await owner.get(f"/api/v1/runs/{run_id}")).json()["state"] == "WAITING_INPUT"
    answered = await owner.post(
        f"/api/v1/input-requests/{again['id']}/answer",
        json={"version": again["version"], "value": True},
    )
    assert answered.status_code == 200, answered.text


async def test_repeated_claim_is_in_doubt_never_claimed_twice(owner, sessions, settings) -> None:
    run_id = await new_run(owner, sessions, settings)
    base = f"/internal/v1/runs/{run_id}"
    await owner.post(f"{base}/handshake", json={"attempt": 1}, headers=SERVICE)
    url = f"{base}/actions/call:row-7/claim"
    results = await asyncio.gather(
        *(owner.post(url, json={"attempt": 1}, headers=SERVICE) for _ in range(4))
    )
    statuses = sorted(r.json()["status"] for r in results)
    assert statuses == ["claimed", "in_doubt", "in_doubt", "in_doubt"]
    done = await owner.post(
        f"{base}/actions/call:row-7/complete",
        json={"attempt": 1, "result": {"sid": "CA9"}},
        headers=SERVICE,
    )
    assert done.json()["status"] == "completed"
    assert (await owner.post(url, json={"attempt": 1}, headers=SERVICE)).json()[
        "status"
    ] == "completed"


async def test_event_stream_does_not_hold_a_database_transaction(owner, sessions, settings) -> None:
    run_id = await new_run(owner, sessions, settings)
    # The in-process transport buffers the whole body, so stream in the background.
    stream = asyncio.create_task(owner.get(f"/api/v1/runs/{run_id}/events"))
    await asyncio.sleep(1.0)  # stream is open and polling
    assert not stream.done()
    async with sessions() as db:
        idle_in_txn = await db.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND state = 'idle in transaction' AND pid <> pg_backend_pid()"
            )
        )
    assert idle_in_txn == 0
    await owner.post(f"/api/v1/runs/{run_id}/cancel")
    await dispatch(sessions, settings)
    body = (await asyncio.wait_for(stream, timeout=10)).text
    assert "event: end" in body and '"CANCELLED"' in body


async def test_runtime_start_happens_without_the_run_lock(owner, sessions, settings) -> None:
    installation = await install_hello(owner)
    await owner.post("/api/v1/runs", json={"installationId": installation["id"]})
    runtime = InertRuntime(sessions)
    await dispatch(sessions, settings, runtime)
    assert runtime.lock_was_free == [True]


async def test_container_started_after_cancel_is_stopped(owner, sessions, settings) -> None:
    installation = await install_hello(owner)
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()

    class CancelDuringStart(InertRuntime):
        async def start_run(self, spec):
            async with sessions() as db, db.begin():  # owner cancels while the container starts
                locked = await service.lock_run(db, spec.run_id)
                await service.request_cancel(db, locked, reason="owner_cancel")
            return "inert-late"

    runtime = CancelDuringStart()
    worker = Worker(sessions, runtime, settings, worker_id="t")
    assert await worker.run_once()  # dispatch
    assert await worker.run_once()  # cancel job
    assert runtime.stopped.count("inert-late") >= 1
    assert (await owner.get(f"/api/v1/runs/{run['id']}")).json()["state"] == "CANCELLED"


def test_capability_token_outlives_the_longest_legal_attempt(settings) -> None:
    class Run:
        active_timeout_seconds = 1800
        max_input_wait_seconds = 86_400

    assert token_ttl_seconds(Run(), settings) >= settings.prepare_timeout_seconds + 1800 + 86_400  # type: ignore[arg-type]


async def test_input_schemas_cannot_use_regex_and_payloads_are_capped(
    owner, sessions, settings
) -> None:
    run_id = await new_run(owner, sessions, settings)
    base = f"/internal/v1/runs/{run_id}"
    await owner.post(f"{base}/handshake", json={"attempt": 1}, headers=SERVICE)
    evil = {**ASK, "schema": {"type": "string", "pattern": "^(a+)+$"}}
    response = await owner.post(
        f"{base}/input-requests", json={"attempt": 1, **evil}, headers=SERVICE
    )
    assert (
        response.status_code == 422 and response.json()["error"]["code"] == "INVALID_INPUT_SCHEMA"
    )
    big = await owner.post(
        f"{base}/events",
        json={
            "attempt": 1,
            "type": "run.log",
            "payload": {"level": "info", "message": "x", "fields": {"a": "b" * 20000}},
        },
        headers=SERVICE,
    )
    assert big.status_code == 422 and big.json()["error"]["code"] == "EVENT_TOO_LARGE"


async def test_manifest_with_catastrophic_pattern_is_rejected(owner) -> None:
    import yaml

    from conftest import ROOT

    manifest = yaml.safe_load((ROOT / "catalog/dev/hello-crew.yaml").read_text())
    manifest["metadata"]["id"] = "regex-bomb"
    manifest["spec"]["configurationSchema"]["properties"]["name"] = {
        "type": "string",
        "pattern": "^(a|aa)+$",
    }
    response = await owner.post("/api/v1/catalog/agents/import", json={"manifest": manifest})
    assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_MANIFEST"
    manifest["spec"]["configurationSchema"]["properties"]["name"] = {
        "type": "string",
        "pattern": "^[A-Za-z0-9_-]{1,64}$",
    }
    assert (
        await owner.post("/api/v1/catalog/agents/import", json={"manifest": manifest})
    ).status_code == 201


async def test_model_signal_cannot_pull_a_run_out_of_waiting_input(
    owner, sessions, settings
) -> None:
    run_id = await new_run(owner, sessions, settings)
    base = f"/internal/v1/runs/{run_id}"
    await owner.post(f"{base}/handshake", json={"attempt": 1}, headers=SERVICE)
    await owner.post(f"{base}/input-requests", json={"attempt": 1, **ASK}, headers=SERVICE)
    response = await owner.post(
        f"{base}/model-state", json={"attempt": 1, "loading": False}, headers=SERVICE
    )
    assert response.json()["state"] == "WAITING_INPUT"


async def test_impossible_cron_is_a_validation_error(owner) -> None:
    response = await owner.post(
        "/api/v1/schedules/preview", json={"cron": "0 0 30 2 *", "timezone": "UTC"}
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_CRON"


async def test_disabled_installation_does_not_start_or_retry(owner, sessions, settings) -> None:
    installation = await install_hello(owner)
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    await owner.patch(
        f"/api/v1/agent-installations/{installation['id']}",
        json={"version": installation["version"], "enabled": False},
    )
    await dispatch(sessions, settings)
    failed = (await owner.get(f"/api/v1/runs/{run['id']}")).json()
    assert failed["state"] == "FAILED" and failed["error"]["code"] == "INSTALLATION_NOT_READY"
    async with sessions() as db, db.begin():
        await db.execute(text("UPDATE agent_runs SET retryable = true"))
    retry = await owner.post(f"/api/v1/runs/{run['id']}/retry")
    assert retry.status_code == 409 and retry.json()["error"]["code"] == "INSTALLATION_NOT_READY"
