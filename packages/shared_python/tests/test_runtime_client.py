from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from crewquarters_shared.runtime import DaemonRuntimeClient, RuntimeStatus

pytestmark = pytest.mark.no_db

REF = "cq-run-00000000-0000-0000-0000-000000000000-1"


def client(handler: httpx.MockTransport) -> DaemonRuntimeClient:
    runtime = DaemonRuntimeClient(Path("/nonexistent.sock"), "token")
    runtime._client = httpx.AsyncClient(transport=handler, base_url="http://runtime-daemon")
    return runtime


async def test_get_run_reads_exit_code_oom_and_memory_limit() -> None:
    body = {
        "runtimeRef": REF,
        "state": "exited",
        "exitCode": 137,
        "oomKilled": True,
        "startedAt": "2026-09-25T10:00:00Z",
        "finishedAt": "2026-09-25T10:00:05Z",
        "memoryLimitBytes": 64 * 1024 * 1024,
    }
    runtime = client(httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    assert await runtime.get_run(REF) == RuntimeStatus(
        "exited",
        137,
        oom_killed=True,
        finished_at="2026-09-25T10:00:05Z",
        memory_limit_bytes=64 * 1024 * 1024,
    )


async def test_get_run_accepts_an_older_daemon_and_a_missing_container() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("-1"):
            return httpx.Response(200, json={"state": "running", "exitCode": None})
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

    runtime = client(httpx.MockTransport(handler))
    assert await runtime.get_run(REF) == RuntimeStatus("running")
    assert await runtime.get_run(REF[:-1] + "2") == RuntimeStatus("missing")
