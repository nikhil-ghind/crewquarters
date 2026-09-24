from __future__ import annotations

import httpx
import pytest

from conftest import install_hello, wait_for_state

pytestmark = pytest.mark.usefixtures("catalog_synced")

SERVICE = {"Authorization": "Bearer dev-insecure-internal-token-change-me-0000"}


async def started_run(owner: httpx.AsyncClient, sessions, settings) -> str:
    """Dispatch a run with a runtime that never runs the agent, so the test acts as the SDK."""
    from crewquarters_scheduler.worker import Worker

    class InertRuntime:
        async def start_run(self, spec):
            return f"inert-{spec.run_id}-{spec.attempt}"

    installation = await install_hello(owner)
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    worker = Worker(sessions, InertRuntime(), settings, worker_id="t")  # type: ignore[arg-type]
    assert await worker.run_once()
    return str(run["id"])


async def test_sdk_protocol_through_internal_api(
    owner: httpx.AsyncClient, sessions, settings
) -> None:
    run_id = await started_run(owner, sessions, settings)
    base = f"/internal/v1/runs/{run_id}"

    status = (await owner.get(base, headers=SERVICE)).json()
    assert status["state"] == "PREPARING" and status["currentAttempt"] == 1
    assert status["modelBindings"] == {"local.general": "local.general.small"}

    assert (await owner.post(f"{base}/handshake", json={"attempt": 1}, headers=SERVICE)).json()[
        "state"
    ] == "RUNNING"
    stale = await owner.post(f"{base}/heartbeat", json={"attempt": 2}, headers=SERVICE)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STALE_ATTEMPT"

    event = await owner.post(
        f"{base}/events",
        json={
            "attempt": 1,
            "type": "run.log",
            "payload": {
                "level": "info",
                "message": "dialing +14155550100",
                "fields": {"apiKey": "sk"},
            },
        },
        headers=SERVICE,
    )
    assert event.status_code == 201
    assert event.json()["payload"]["message"] == "dialing ***0100"
    assert event.json()["payload"]["fields"]["apiKey"] == "[REDACTED]"
    forbidden = await owner.post(
        f"{base}/events",
        json={"attempt": 1, "type": "run.state_changed", "payload": {}},
        headers=SERVICE,
    )
    assert forbidden.status_code == 422

    claim = (
        await owner.post(f"{base}/actions/call:row-2/claim", json={"attempt": 1}, headers=SERVICE)
    ).json()
    assert claim["status"] == "claimed"
    done = (
        await owner.post(
            f"{base}/actions/call:row-2/complete",
            json={"attempt": 1, "result": {"sid": "CA1"}},
            headers=SERVICE,
        )
    ).json()
    assert done == {"key": "call:row-2", "status": "completed", "result": {"sid": "CA1"}}
    again = (
        await owner.post(f"{base}/actions/call:row-2/claim", json={"attempt": 1}, headers=SERVICE)
    ).json()
    assert again["status"] == "completed" and again["result"] == {"sid": "CA1"}

    ask = await owner.post(
        f"{base}/input-requests",
        json={
            "attempt": 1,
            "key": "confirm",
            "title": "Go?",
            "prompt": "Proceed?",
            "schema": {"type": "string", "enum": ["yes", "no"]},
            "timeoutSeconds": 600,
        },
        headers=SERVICE,
    )
    assert ask.status_code == 200, ask.text
    request = ask.json()
    over_budget = await owner.post(
        f"{base}/input-requests",
        json={
            "attempt": 1,
            "key": "other",
            "title": "t",
            "prompt": "p",
            "schema": {},
            "timeoutSeconds": 86400,
        },
        headers=SERVICE,
    )
    assert (
        over_budget.status_code == 422
        and over_budget.json()["error"]["code"] == "INPUT_WAIT_BUDGET_EXCEEDED"
    )

    poll = (
        await owner.get(
            f"/internal/v1/input-requests/{request['id']}", params={"wait": 0.2}, headers=SERVICE
        )
    ).json()
    assert poll["state"] == "pending"
    await owner.post(
        f"/api/v1/input-requests/{request['id']}/answer", json={"version": 1, "value": "yes"}
    )
    poll = (await owner.get(f"/internal/v1/input-requests/{request['id']}", headers=SERVICE)).json()
    assert poll["state"] == "answered" and poll["answer"] == "yes"

    loading = await owner.post(
        f"{base}/model-state",
        json={"attempt": 1, "loading": True, "model": "local.general.small"},
        headers=SERVICE,
    )
    assert loading.json()["state"] == "LOADING_MODEL"
    await owner.post(f"{base}/model-state", json={"attempt": 1, "loading": False}, headers=SERVICE)

    result = await owner.post(
        f"{base}/result",
        json={"attempt": 1, "status": "succeeded", "result": {"ok": True}},
        headers=SERVICE,
    )
    assert result.json()["state"] == "SUCCEEDED"
    duplicate = await owner.post(
        f"{base}/result", json={"attempt": 1, "status": "failed"}, headers=SERVICE
    )
    assert duplicate.json()["state"] == "SUCCEEDED"  # first result wins


async def test_retried_attempt_reuses_answered_input(
    owner: httpx.AsyncClient, sessions, settings
) -> None:
    run_id = await started_run(owner, sessions, settings)
    base = f"/internal/v1/runs/{run_id}"
    await owner.post(f"{base}/handshake", json={"attempt": 1}, headers=SERVICE)
    ask_body = {
        "attempt": 1,
        "key": "confirm",
        "title": "Go?",
        "prompt": "Proceed?",
        "schema": {"type": "boolean"},
        "timeoutSeconds": 600,
    }
    request = (await owner.post(f"{base}/input-requests", json=ask_body, headers=SERVICE)).json()
    await owner.post(
        f"/api/v1/input-requests/{request['id']}/answer", json={"version": 1, "value": True}
    )
    await owner.post(
        f"{base}/result",
        json={
            "attempt": 1,
            "status": "failed",
            "error": {"code": "BOOM", "message": "x", "retryable": True},
        },
        headers=SERVICE,
    )
    await owner.post(f"/api/v1/runs/{run_id}/retry")

    from crewquarters_scheduler.worker import Worker

    class InertRuntime:
        async def start_run(self, spec):
            return "inert"

    await Worker(sessions, InertRuntime(), settings, worker_id="t").run_once()  # type: ignore[arg-type]
    await owner.post(f"{base}/handshake", json={"attempt": 2}, headers=SERVICE)
    again = (
        await owner.post(f"{base}/input-requests", json={**ask_body, "attempt": 2}, headers=SERVICE)
    ).json()
    assert again["id"] == request["id"] and again["state"] == "answered" and again["answer"] is True
    run = await wait_for_state(owner, run_id, {"RUNNING"})
    assert run["currentAttempt"] == 2 and run["pendingInputCount"] == 0
