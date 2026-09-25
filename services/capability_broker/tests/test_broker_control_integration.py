"""The broker against the real control API: a real run, a real token, real run state."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

from crewquarters_broker.config import BrokerSettings
from crewquarters_broker.main import create_app
from crewquarters_shared.runtime import RunSpec

if TYPE_CHECKING:
    from broker_testkit import Harness

pytest_plugins = ["broker_testkit"]


async def _wait_state(owner: httpx.AsyncClient, run_id: str, state: str) -> None:
    for _ in range(200):
        if (await owner.get(f"/api/v1/runs/{run_id}")).json()["state"] == state:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"run never reached {state}")


async def test_agent_lifecycle_through_broker(
    owner: httpx.AsyncClient,
    app: Any,
    platform: Any,
    catalog_synced: None,
    broker_settings: BrokerSettings,
    harness: Harness,
) -> None:
    specs: list[RunSpec] = []
    start = platform.runtime.start_run

    async def capture(spec: RunSpec) -> str:
        specs.append(spec)
        return str(await start(spec))

    platform.runtime.start_run = capture
    install = await owner.post(
        "/api/v1/agent-installations",
        json={
            "agentId": "hello-crew",
            "config": {"fakeScenario": "slow"},
            "approvedPermissions": {
                "llmProfiles": ["local.general"],
                "knowledge": [],
                "connectors": {},
                "cloudProviders": [],
                "userInput": True,
            },
        },
    )
    assert install.status_code == 201, install.text
    run_id = (
        await owner.post("/api/v1/runs", json={"installationId": install.json()["id"]})
    ).json()["id"]
    await _wait_state(owner, run_id, "RUNNING")
    token = specs[0].env["PLATFORM_RUN_TOKEN"]

    broker = create_app(broker_settings, control_transport=httpx.ASGITransport(app=app))
    headers = {"authorization": f"Bearer {token}"}
    async with (
        broker.router.lifespan_context(broker),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=broker), base_url="http://b") as b,
    ):
        beat = await b.post("/agent/v1/heartbeat", headers=headers)
        assert beat.status_code == 200, beat.text
        assert beat.json()["cancelRequested"] is False

        event = await b.post(
            "/agent/v1/events",
            headers=headers,
            json={"type": "run.log", "payload": {"message": "via broker"}},
        )
        assert event.status_code in (200, 201, 204), event.text

        asked = await b.post(
            "/agent/v1/input-requests",
            headers=headers,
            json={
                "key": "confirm",
                "title": "Confirm",
                "prompt": "Proceed?",
                "schema": {"type": "boolean"},
                "timeoutSeconds": 60,
            },
        )
        assert asked.status_code in (200, 201), asked.text
        polled = await b.get(f"/agent/v1/input-requests/{asked.json()['id']}", headers=headers)
        assert polled.status_code == 200 and polled.json()["state"] == "pending"

        claim = await b.post("/agent/v1/actions/call-row-1/claim", headers=headers)
        assert claim.json()["status"] == "claimed", claim.text
        done = await b.post(
            "/agent/v1/actions/call-row-1/complete",
            headers=headers,
            json={"result": {"sid": "CA1"}},
        )
        assert done.status_code == 200, done.text
        again = await b.post("/agent/v1/actions/call-row-1/claim", headers=headers)
        assert again.json() == {
            "key": "call-row-1",
            "status": "completed",
            "result": {"sid": "CA1"},
        }

        denied = await b.get("/agent/v1/google/gmail/messages", headers=headers)
        assert denied.status_code == 403

        # Cancellation propagates, and a terminal run revokes the token.
        cancel = await owner.post(f"/api/v1/runs/{run_id}/cancel")
        assert cancel.status_code in (200, 202), cancel.text
        await _wait_state(owner, run_id, "CANCELLED")
        after = await b.post("/agent/v1/heartbeat", headers=headers)
        assert after.status_code == 409 and after.json()["error"]["code"] == "RUN_NOT_ACTIVE"
