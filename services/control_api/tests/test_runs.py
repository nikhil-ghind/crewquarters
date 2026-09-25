from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from conftest import install_hello, wait_for_state

pytestmark = pytest.mark.usefixtures("catalog_synced")


async def read_sse(
    owner: httpx.AsyncClient, run_id: str, headers: dict[str, str] | None = None
) -> list[dict]:
    events: list[dict] = []
    async with owner.stream(
        "GET", f"/api/v1/runs/{run_id}/events", headers=headers or {}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        block: dict[str, str] = {}
        async for line in response.aiter_lines():
            if line == "":
                if block.get("event") == "end":
                    return events
                if "data" in block and block.get("event") != "end":
                    events.append(
                        {
                            "id": int(block["id"]),
                            "event": block["event"],
                            "data": json.loads(block["data"]),
                        }
                    )
                block = {}
                continue
            if line.startswith(":"):
                continue
            key, _, value = line.partition(": ")
            block[key] = value
    return events


async def test_manual_run_succeeds_and_streams_events(owner: httpx.AsyncClient, platform) -> None:
    installation = await install_hello(owner)
    created = await owner.post("/api/v1/runs", json={"installationId": installation["id"]})
    assert created.status_code == 201, created.text
    run = created.json()
    assert run["state"] == "QUEUED" and run["currentAttempt"] == 1

    events = await asyncio.wait_for(read_sse(owner, run["id"]), timeout=15)
    states = [e["data"]["payload"]["to"] for e in events if e["event"] == "run.state_changed"]
    assert states == ["QUEUED", "PREPARING", "RUNNING", "SUCCEEDED"]
    assert [e["id"] for e in events] == list(range(1, len(events) + 1))
    assert any(e["event"] == "run.progress" for e in events)

    final = (await owner.get(f"/api/v1/runs/{run['id']}")).json()
    assert final["state"] == "SUCCEEDED"
    assert final["result"] == {"message": "Hello from the fake runtime."}

    # Reconnecting with Last-Event-ID replays only newer events.
    tail = await read_sse(owner, run["id"], headers={"Last-Event-ID": str(events[-2]["id"])})
    assert [e["id"] for e in tail] == [events[-1]["id"]]


async def test_input_request_round_trip(owner: httpx.AsyncClient, platform) -> None:
    installation = await install_hello(owner, {"fakeScenario": "ask"})
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    await wait_for_state(owner, run["id"], {"WAITING_INPUT"})

    pending = (await owner.get("/api/v1/input-requests")).json()["items"]
    assert len(pending) == 1
    request = pending[0]
    assert request["agentName"] == "Hello Crew" and request["state"] == "pending"

    invalid = await owner.post(
        f"/api/v1/input-requests/{request['id']}/answer",
        json={"version": request["version"], "value": {"decision": "maybe"}},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_ANSWER"

    stale = await owner.post(
        f"/api/v1/input-requests/{request['id']}/answer",
        json={"version": request["version"] + 1, "value": {"decision": "continue"}},
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"

    answered = await owner.post(
        f"/api/v1/input-requests/{request['id']}/answer",
        json={"version": request["version"], "value": {"decision": "continue"}},
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["state"] == "answered"

    double = await owner.post(
        f"/api/v1/input-requests/{request['id']}/answer",
        json={"version": request["version"], "value": {"decision": "continue"}},
    )
    assert double.status_code == 409 and double.json()["error"]["code"] == "INPUT_ALREADY_CLOSED"

    final = await wait_for_state(owner, run["id"], {"SUCCEEDED"})
    assert final["result"]["decision"] == "continue"
    assert final["inputWaitSecondsUsed"] > 0


async def test_cancel_running_run(owner: httpx.AsyncClient, platform) -> None:
    installation = await install_hello(owner, {"fakeScenario": "slow"})
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    await wait_for_state(owner, run["id"], {"RUNNING"})
    cancelled = await owner.post(f"/api/v1/runs/{run['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "CANCELLING"
    await wait_for_state(owner, run["id"], {"CANCELLED"})
    again = await owner.post(f"/api/v1/runs/{run['id']}/cancel")
    assert again.status_code == 200 and again.json()["state"] == "CANCELLED"


async def test_cancel_queued_run_never_starts(owner: httpx.AsyncClient) -> None:
    installation = await install_hello(owner)
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    cancelled = await owner.post(f"/api/v1/runs/{run['id']}/cancel")
    assert cancelled.json()["state"] == "CANCELLED"
    retry = await owner.post(f"/api/v1/runs/{run['id']}/retry")
    assert retry.status_code == 409


async def test_failed_run_can_be_retried_as_new_attempt(owner: httpx.AsyncClient, platform) -> None:
    installation = await install_hello(owner, {"fakeScenario": "fail"})
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    failed = await wait_for_state(owner, run["id"], {"FAILED"})
    assert failed["error"]["code"] == "FAKE_FAILURE" and failed["retryable"] is True

    retried = await owner.post(f"/api/v1/runs/{run['id']}/retry")
    assert retried.status_code == 200
    assert retried.json()["state"] == "QUEUED" and retried.json()["currentAttempt"] == 2
    await wait_for_state(owner, run["id"], {"FAILED"})
    assert platform.runtime.started == [f"fake-{run['id']}-1", f"fake-{run['id']}-2"]


async def test_lost_heartbeat_interrupts_run(owner: httpx.AsyncClient, platform) -> None:
    """A container that is still running but never hands shake (or stops heartbeating)."""
    installation = await install_hello(owner, {"fakeScenario": "hang"})
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    interrupted = await wait_for_state(owner, run["id"], {"INTERRUPTED"}, within=15)
    assert interrupted["error"]["code"] == "HEARTBEAT_LOST"
    assert interrupted["retryable"] is True


async def test_model_loading_state_is_visible(owner: httpx.AsyncClient, platform) -> None:
    installation = await install_hello(owner, {"fakeScenario": "model", "fakeStepSeconds": 0.3})
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    await wait_for_state(owner, run["id"], {"SUCCEEDED"})
    history = (await owner.get(f"/api/v1/runs/{run['id']}/events/history")).json()
    states = [e["payload"]["to"] for e in history if e["type"] == "run.state_changed"]
    assert states == ["QUEUED", "PREPARING", "RUNNING", "LOADING_MODEL", "RUNNING", "SUCCEEDED"]


async def test_idempotency_key_replays_and_rejects_reuse(owner: httpx.AsyncClient) -> None:
    installation = await install_hello(owner)
    headers = {"Idempotency-Key": "run-once-0001"}
    first = await owner.post(
        "/api/v1/runs", json={"installationId": installation["id"]}, headers=headers
    )
    second = await owner.post(
        "/api/v1/runs", json={"installationId": installation["id"]}, headers=headers
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.headers.get("idempotent-replayed") == "true"
    listed = (await owner.get("/api/v1/runs")).json()["items"]
    assert len(listed) == 1

    other = await owner.post(
        "/api/v1/runs",
        json={"installationId": "00000000-0000-0000-0000-000000000000"},
        headers=headers,
    )
    assert other.status_code == 409 and other.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


async def test_runs_pagination(owner: httpx.AsyncClient) -> None:
    installation = await install_hello(owner)
    ids = [
        (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()["id"]
        for _ in range(5)
    ]
    page1 = (await owner.get("/api/v1/runs", params={"limit": 2})).json()
    page2 = (
        await owner.get("/api/v1/runs", params={"limit": 2, "cursor": page1["nextCursor"]})
    ).json()
    page3 = (
        await owner.get("/api/v1/runs", params={"limit": 2, "cursor": page2["nextCursor"]})
    ).json()
    seen = [r["id"] for r in page1["items"] + page2["items"] + page3["items"]]
    assert seen == list(reversed(ids))
    assert page3["nextCursor"] is None
