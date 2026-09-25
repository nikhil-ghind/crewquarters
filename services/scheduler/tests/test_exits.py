"""Exit watcher (ADR 0006, revision 1): container exits are read from the runtime.

The first tests run the whole loop (worker, reconciler, exit watcher) against the fake
runtime's scenarios. The rest freeze the loops and drive ``exits.tick`` with a stub runtime,
so each observed status is deterministic.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from conftest import install_hello, wait_for_state
from crewquarters_scheduler import exits
from crewquarters_shared.db.models import Job, RunAttempt
from crewquarters_shared.runtime import RunSpec, RuntimeStatus
from crewquarters_shared.timeutil import utcnow

pytestmark = pytest.mark.usefixtures("catalog_synced")


class StubRuntime:
    """Answers ``get_run`` from a dict; anything else is unexpected."""

    def __init__(self, statuses: dict[str, RuntimeStatus | Exception]) -> None:
        self.statuses = statuses
        self.asked: list[str] = []

    async def get_run(self, runtime_ref: str) -> RuntimeStatus:
        self.asked.append(runtime_ref)
        status = self.statuses.get(runtime_ref, RuntimeStatus("missing"))
        if isinstance(status, Exception):
            raise status
        return status

    async def start_run(self, spec: RunSpec) -> str:
        raise AssertionError("unexpected")

    async def stop_run(self, runtime_ref: str, grace_seconds: int) -> None:
        raise AssertionError("unexpected")

    async def capacity(self) -> dict[str, Any]:
        return {}

    async def close(self) -> None:
        return None


# Scenarios the fake runtime supports but hello-crew's (immutable) manifest does not offer.
TEST_ONLY_SCENARIOS = ("oom", "exit0")


async def start(owner: httpx.AsyncClient, scenario: str, platform: Any = None) -> dict[str, Any]:
    if scenario in TEST_ONLY_SCENARIOS:
        installation = await install_hello(owner)
        platform.runtime.forced_scenarios[installation["id"]] = scenario
    else:
        installation = await install_hello(owner, {"fakeScenario": scenario})
    response = await owner.post("/api/v1/runs", json={"installationId": installation["id"]})
    assert response.status_code == 201, response.text
    return dict(response.json())


async def attempts(sessions, run_id: str) -> list[RunAttempt]:
    async with sessions() as db:
        rows = await db.scalars(
            select(RunAttempt).where(RunAttempt.run_id == run_id).order_by(RunAttempt.attempt)
        )
        return list(rows)


async def stop_jobs(sessions) -> int:
    async with sessions() as db:
        return int(await db.scalar(select(func.count()).where(Job.type == "run.stop")) or 0)


# --- Through the running loops (fake runtime) --------------------------------------------


async def test_crash_before_handshake_fails_within_seconds(
    owner: httpx.AsyncClient, sessions, platform
) -> None:
    """The container exits 1 before its handshake. Without the watcher this waited out the
    prepare lease (5 s here, 600 s by default) and became INTERRUPTED/HEARTBEAT_LOST."""
    run = await start(owner, "crash")
    loop = asyncio.get_running_loop()
    began = loop.time()
    failed = await wait_for_state(owner, run["id"], {"FAILED", "INTERRUPTED"}, within=10)
    assert loop.time() - began < 3, "detected by the lease, not the exit watcher"
    assert failed["state"] == "FAILED"
    assert failed["error"]["code"] == "AGENT_EXITED"
    assert "exited with code 1 before its handshake" in failed["error"]["message"]
    assert failed["error"]["details"] == {"exitCode": 1, "oomKilled": False}
    assert failed["retryable"] is True
    [attempt] = await attempts(sessions, run["id"])
    assert (attempt.exit_code, attempt.state) == (1, "exited")
    assert attempt.error and attempt.error["code"] == "AGENT_EXITED"
    assert await stop_jobs(sessions) == 1  # the exited container is removed

    retried = await owner.post(f"/api/v1/runs/{run['id']}/retry")
    assert retried.status_code == 200 and retried.json()["currentAttempt"] == 2


async def test_out_of_memory_fails_with_the_memory_limit(
    owner: httpx.AsyncClient, sessions, platform
) -> None:
    run = await start(owner, "oom", platform)
    failed = await wait_for_state(owner, run["id"], {"FAILED", "INTERRUPTED"}, within=10)
    assert failed["state"] == "FAILED"
    error = failed["error"]
    assert error["code"] == "AGENT_OUT_OF_MEMORY"
    assert "256 MiB memory limit" in error["message"] and "137" in error["message"]
    assert error["details"] == {"exitCode": 137, "oomKilled": True, "memoryLimitMb": 256}
    assert failed["retryable"] is True
    [attempt] = await attempts(sessions, run["id"])
    assert attempt.exit_code == 137


async def test_exit_zero_without_result_fails(owner: httpx.AsyncClient, sessions, platform) -> None:
    run = await start(owner, "exit0", platform)
    failed = await wait_for_state(owner, run["id"], {"FAILED", "INTERRUPTED"}, within=10)
    assert failed["error"]["code"] == "AGENT_EXITED_WITHOUT_RESULT"
    assert failed["state"] == "FAILED" and failed["retryable"] is True
    [attempt] = await attempts(sessions, run["id"])
    assert attempt.exit_code == 0


async def test_result_then_exit_stays_succeeded_and_records_the_exit_code(
    owner: httpx.AsyncClient, sessions, platform
) -> None:
    """The SDK posts its result, then exits 0: the watcher only records the exit code."""
    run = await start(owner, "succeed")
    await wait_for_state(owner, run["id"], {"SUCCEEDED"})
    for _ in range(100):
        [attempt] = await attempts(sessions, run["id"])
        if attempt.exit_code is not None:
            break
        await asyncio.sleep(0.05)
    assert attempt.exit_code == 0
    final = (await owner.get(f"/api/v1/runs/{run['id']}")).json()
    assert final["state"] == "SUCCEEDED" and final["error"] is None


async def test_agent_reported_failure_keeps_its_own_error(
    owner: httpx.AsyncClient, sessions, platform
) -> None:
    run = await start(owner, "fail")
    failed = await wait_for_state(owner, run["id"], {"FAILED"})
    await asyncio.sleep(0.5)  # several watcher ticks see the exit (code 1)
    final = (await owner.get(f"/api/v1/runs/{run['id']}")).json()
    assert final["error"]["code"] == failed["error"]["code"] == "FAKE_FAILURE"
    [attempt] = await attempts(sessions, run["id"])
    assert attempt.exit_code == 1


# --- Stub runtime, loops frozen ------------------------------------------------------------


async def running(owner: httpx.AsyncClient, sessions, platform) -> tuple[dict[str, Any], str]:
    """A RUNNING run (the fake keeps heartbeating) with the background loops stopped."""
    run = await start(owner, "slow")
    await wait_for_state(owner, run["id"], {"RUNNING"})
    platform.stop.set()
    await asyncio.gather(*platform.tasks, return_exceptions=True)
    [attempt] = await attempts(sessions, run["id"])
    assert attempt.runtime_ref
    return run, attempt.runtime_ref


async def test_a_running_container_is_left_alone(
    owner: httpx.AsyncClient, sessions, platform
) -> None:
    run, ref = await running(owner, sessions, platform)
    stub = StubRuntime({ref: RuntimeStatus("running")})
    report = await exits.tick(sessions, stub, utcnow())
    assert stub.asked == [ref] and report.exited == 0
    assert (await owner.get(f"/api/v1/runs/{run['id']}")).json()["state"] == "RUNNING"


@pytest.mark.parametrize(
    "status", [RuntimeStatus("missing"), ConnectionError("daemon restarting")], ids=str
)
async def test_missing_container_or_unreachable_runtime_is_left_to_the_lease(
    owner: httpx.AsyncClient, sessions, platform, status: RuntimeStatus | Exception
) -> None:
    run, ref = await running(owner, sessions, platform)
    report = await exits.tick(sessions, StubRuntime({ref: status}), utcnow())
    assert report.exited == 0
    assert report.errors == (1 if isinstance(status, Exception) else 0)
    assert (await owner.get(f"/api/v1/runs/{run['id']}")).json()["state"] == "RUNNING"
    [attempt] = await attempts(sessions, run["id"])
    assert attempt.exit_code is None


async def test_oom_without_a_reported_limit_uses_the_manifest(
    owner: httpx.AsyncClient, sessions, platform
) -> None:
    run, ref = await running(owner, sessions, platform)
    stub = StubRuntime({ref: RuntimeStatus("exited", 137, oom_killed=True)})
    report = await exits.tick(sessions, stub, utcnow())
    assert report.failed == {"AGENT_OUT_OF_MEMORY": 1}
    final = (await owner.get(f"/api/v1/runs/{run['id']}")).json()
    assert final["state"] == "FAILED"
    assert final["error"]["details"]["memoryLimitMb"] == 256

    # The attempt has its exit code now, so the next tick no longer asks about it.
    stub.asked.clear()
    await exits.tick(sessions, stub, utcnow())
    assert stub.asked == []


async def test_an_unflagged_sigkill_is_a_probable_out_of_memory_kill(
    owner: httpx.AsyncClient, sessions, platform
) -> None:
    """Docker sometimes loses OOMKilled (exit 137, OOMKilled=false)."""
    run, ref = await running(owner, sessions, platform)
    status = RuntimeStatus("exited", 137, memory_limit_bytes=64 << 20)
    await exits.tick(sessions, StubRuntime({ref: status}), utcnow())
    final = (await owner.get(f"/api/v1/runs/{run['id']}")).json()
    assert (final["state"], final["error"]["code"]) == ("FAILED", "AGENT_OUT_OF_MEMORY")
    assert "SIGKILL" in final["error"]["message"] and "64 MiB" in final["error"]["message"]
    assert final["error"]["details"] == {"exitCode": 137, "oomKilled": False, "memoryLimitMb": 64}


async def test_other_signals_are_plain_exits(owner: httpx.AsyncClient, sessions, platform) -> None:
    run, ref = await running(owner, sessions, platform)
    await exits.tick(sessions, StubRuntime({ref: RuntimeStatus("exited", 139)}), utcnow())
    final = (await owner.get(f"/api/v1/runs/{run['id']}")).json()
    assert final["error"]["code"] == "AGENT_EXITED"
    assert "exited with code 139 without" in final["error"]["message"]


async def test_a_waiting_run_whose_agent_crashed_fails_and_closes_its_questions(
    owner: httpx.AsyncClient, sessions, platform
) -> None:
    run = await start(owner, "ask")
    await wait_for_state(owner, run["id"], {"WAITING_INPUT"})
    platform.stop.set()
    await asyncio.gather(*platform.tasks, return_exceptions=True)
    [attempt] = await attempts(sessions, run["id"])
    assert attempt.runtime_ref
    await exits.tick(
        sessions, StubRuntime({attempt.runtime_ref: RuntimeStatus("exited", 2)}), utcnow()
    )
    final = (await owner.get(f"/api/v1/runs/{run['id']}")).json()
    assert (final["state"], final["error"]["code"]) == ("FAILED", "AGENT_EXITED")
    assert "exited with code 2 without" in final["error"]["message"]
    cancelled = (await owner.get("/api/v1/input-requests", params={"state": "cancelled"})).json()
    assert len(cancelled["items"]) == 1


async def test_an_old_attempts_exit_does_not_fail_the_current_attempt(
    owner: httpx.AsyncClient, sessions, platform
) -> None:
    """Attempt 1 crashed and was retried; its container's late exit status only records the
    exit code of attempt 1."""
    from crewquarters_shared.runs import service

    run, ref = await running(owner, sessions, platform)
    async with sessions() as db, db.begin():
        row = await service.lock_run(db, run["id"])
        await service.fail_run(db, row, "HEARTBEAT_LOST", "lost", retryable=True, interrupted=True)
    assert (await owner.post(f"/api/v1/runs/{run['id']}/retry")).status_code == 200
    async with sessions() as db, db.begin():
        row = await service.lock_run(db, run["id"])
        await service.begin_attempt(db, row, 60)
    async with sessions() as db, db.begin():
        code = await service.record_container_exit(
            db, row.id, 1, ref, exit_code=137, oom_killed=True
        )
    assert code is None
    final = (await owner.get(f"/api/v1/runs/{run['id']}")).json()
    assert (final["state"], final["currentAttempt"]) == ("PREPARING", 2)
    first, _second = await attempts(sessions, run["id"])
    assert first.exit_code == 137
