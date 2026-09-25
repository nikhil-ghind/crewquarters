"""Internal run API additions for the capability broker: the full run view for the SDK
handshake, atomic deduplicated event batches, retry-safe action claims, and heartbeats on
finished runs."""

from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
import pytest

from conftest import install_hello

pytestmark = pytest.mark.usefixtures("catalog_synced")

SERVICE = {"Authorization": "Bearer dev-insecure-internal-token-change-me-0000"}


async def started_run(owner: httpx.AsyncClient, sessions, settings) -> str:  # type: ignore[no-untyped-def]
    from crewquarters_scheduler.worker import Worker

    class InertRuntime:
        async def start_run(self, spec):  # type: ignore[no-untyped-def]
            return f"inert-{spec.run_id}-{spec.attempt}"

    installation = await install_hello(owner)
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    worker = Worker(sessions, InertRuntime(), settings, worker_id="t")  # type: ignore[arg-type]
    assert await worker.run_once()
    base = f"/internal/v1/runs/{run['id']}"
    shook = await owner.post(f"{base}/handshake", json={"attempt": 1}, headers=SERVICE)
    assert shook.status_code == 200, shook.text
    return str(run["id"])


def event(cid: str, message: str = "hi", **extra: object) -> dict[str, object]:
    return {
        "clientEventId": cid,
        "type": "run.log",
        "occurredAt": "2026-09-25T10:00:00Z",
        "payload": {"level": "info", "message": message},
        **extra,
    }


async def test_run_view_has_what_the_handshake_needs(owner, sessions, settings) -> None:  # type: ignore[no-untyped-def]
    run_id = await started_run(owner, sessions, settings)
    view = (await owner.get(f"/internal/v1/runs/{run_id}", headers=SERVICE)).json()
    assert view["trigger"] == "manual" and view["scheduledFor"] is None
    assert view["agentId"] == "hello-crew" and view["agentVersion"] == "0.1.0"
    assert view["agentVersionId"] and datetime.fromisoformat(view["createdAt"])
    assert view["activeTimeoutSeconds"] == 600
    assert 0 < view["activeSecondsRemaining"] <= 600
    assert view["maxInputWaitSeconds"] == 3600
    assert view["inputWaitRemainingSeconds"] == 3600


async def test_event_batch_is_atomic_deduplicated_and_keeps_occurred_at(
    owner: httpx.AsyncClient, sessions, settings
) -> None:  # type: ignore[no-untyped-def]
    run_id = await started_run(owner, sessions, settings)
    url = f"/internal/v1/runs/{run_id}/event-batches"
    history = f"/api/v1/runs/{run_id}/events/history"
    before = len((await owner.get(history)).json())

    big = event("big", message="x" * 17_000)
    bad = {**event("bad"), "payload": {"level": "loud", "message": "m"}}
    rejected = await owner.post(
        url,
        json={"attempt": 1, "events": [event("a"), big, event("b"), bad]},
        headers=SERVICE,
    )
    assert rejected.status_code == 422, rejected.text
    error = rejected.json()["error"]
    assert error["code"] == "INVALID_EVENT"
    assert [(r["index"], r["clientEventId"], r["code"]) for r in error["details"]["rejected"]] == [
        (1, "big", "EVENT_TOO_LARGE"),
        (3, "bad", "INVALID_EVENT"),
    ]
    assert len((await owner.get(history)).json()) == before  # nothing stored

    # The SDK's salvage: resend event by event. Valid events are stored exactly once.
    for item in (event("a"), big, event("b"), bad):
        await owner.post(url, json={"attempt": 1, "events": [item]}, headers=SERVICE)
    single = await owner.post(url, json={"attempt": 1, "events": [big]}, headers=SERVICE)
    assert single.json()["error"]["code"] == "EVENT_TOO_LARGE"

    # A retried batch (lost response) and in-batch repeats store nothing new.
    retry = await owner.post(
        url,
        json={"attempt": 1, "events": [event("a"), event("b"), event("c"), event("c")]},
        headers=SERVICE,
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["accepted"] == 1 and retry.json()["duplicates"] == 3

    events = (await owner.get(history)).json()[before:]
    assert [e["payload"]["message"] for e in events] == ["hi", "hi", "hi"]
    assert retry.json()["lastSequence"] == events[-1]["sequence"]
    assert all(e["occurredAt"].startswith("2026-09-25T10:00:00") for e in events)
    assert all(e["occurredAt"] != e["createdAt"] for e in events)

    # Each call is one transaction; concurrent retries of the same batch never duplicate.
    same = {"attempt": 1, "events": [event("d"), event("e")]}
    results = await asyncio.gather(*(owner.post(url, json=same, headers=SERVICE) for _ in range(3)))
    assert sorted(r.json()["accepted"] for r in results) == [0, 0, 2]

    stale = await owner.post(url, json={"attempt": 2, "events": [event("z")]}, headers=SERVICE)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STALE_ATTEMPT"
    wrong_type = await owner.post(
        url, json={"attempt": 1, "events": [event("t", type="run.result")]}, headers=SERVICE
    )
    assert wrong_type.status_code == 422
    assert wrong_type.json()["error"]["code"] == "INVALID_EVENT_TYPE"


async def test_retried_claim_with_the_same_token_is_still_claimed(
    owner: httpx.AsyncClient, sessions, settings
) -> None:  # type: ignore[no-untyped-def]
    run_id = await started_run(owner, sessions, settings)
    url = f"/internal/v1/runs/{run_id}/actions/call:row-1/claim"
    first = await owner.post(
        url, json={"attempt": 1, "claimToken": "tok-aaaaaaaa"}, headers=SERVICE
    )
    assert first.json() == {"key": "call:row-1", "status": "claimed", "result": None}
    retry = await owner.post(
        url, json={"attempt": 1, "claimToken": "tok-aaaaaaaa"}, headers=SERVICE
    )
    assert retry.json()["status"] == "claimed"
    other = await owner.post(
        url, json={"attempt": 1, "claimToken": "tok-bbbbbbbb"}, headers=SERVICE
    )
    assert other.json()["status"] == "in_doubt"
    # Once in doubt, even the original claimant must check the provider first.
    late = await owner.post(url, json={"attempt": 1, "claimToken": "tok-aaaaaaaa"}, headers=SERVICE)
    assert late.json()["status"] == "in_doubt"
    anonymous = await owner.post(
        f"/internal/v1/runs/{run_id}/actions/call:row-2/claim",
        json={"attempt": 1},
        headers=SERVICE,
    )
    assert anonymous.json()["status"] == "claimed"
    again = await owner.post(
        f"/internal/v1/runs/{run_id}/actions/call:row-2/claim",
        json={"attempt": 1},
        headers=SERVICE,
    )
    assert again.json()["status"] == "in_doubt"  # no token: current semantics


async def test_heartbeat_on_a_finished_run_requests_cancel(
    owner: httpx.AsyncClient, sessions, settings
) -> None:  # type: ignore[no-untyped-def]
    run_id = await started_run(owner, sessions, settings)
    base = f"/internal/v1/runs/{run_id}"
    done = await owner.post(
        f"{base}/result", json={"attempt": 1, "status": "succeeded", "result": {}}, headers=SERVICE
    )
    assert done.json()["state"] == "SUCCEEDED"
    beat = await owner.post(f"{base}/heartbeat", json={"attempt": 1}, headers=SERVICE)
    assert beat.status_code == 200
    assert beat.json() == {"state": "SUCCEEDED", "cancelRequested": True}
