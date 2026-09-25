"""The broker's control-plane paths: the handshake built from the control API's run view,
whole event batches, retry-safe action claims, and heartbeats on finished runs. The last
tests drive Person 5's SDK clients through the broker against the real control API."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from crewquarters._transport import BrokerClient
from crewquarters.events import EventsClient
from crewquarters.idempotency import IdempotencyClient
from crewquarters_broker.config import BrokerSettings
from crewquarters_broker.main import create_app
from crewquarters_shared.runtime import RunSpec

SDK = "/internal/v1/sdk"
HELLO = {
    "agentId": "hello-crew",
    "config": {"fakeScenario": "slow"},
    "approvedPermissions": {
        "llmProfiles": ["local.general"],
        "knowledge": [],
        "connectors": {},
        "cloudProviders": [],
        "userInput": True,
    },
}


def _log(cid: str, message: str = "hi") -> dict[str, Any]:
    return {
        "clientEventId": cid,
        "type": "run.log",
        "occurredAt": "2026-09-25T10:00:00Z",
        "payload": {"level": "info", "message": message},
    }


async def test_handshake_uses_the_run_view(harness: Any) -> None:
    headers = harness.agent(["events.write"])
    harness.run.update(
        {
            "trigger": "schedule",
            "scheduledFor": "2026-09-25T04:30:00+00:00",
            "agentId": "gmail-digest",
            "agentVersion": "1.2.3",
            "createdAt": "2026-09-25T04:30:01+00:00",
            "activeTimeoutSeconds": 900,
            "inputWaitRemainingSeconds": 120.5,
        }
    )
    resp = await harness.client.post(
        f"{SDK}/handshake",
        headers=headers,
        json={"protocol": "v1alpha1", "sdkVersion": "0.1.0", "agentId": "spoofed"},
    )
    assert resp.status_code == 200, resp.text
    run = resp.json()["run"]
    assert run["trigger"] == "schedule" and run["scheduledFor"] == "2026-09-25T04:30:00+00:00"
    assert run["agentId"] == "gmail-digest" and run["agentVersion"] == "1.2.3"
    assert run["createdAt"] == "2026-09-25T04:30:01+00:00"
    assert resp.json()["limits"] == {
        "activeTimeoutSeconds": 900,
        "inputWaitRemainingSeconds": 120.5,
    }


async def test_event_rejections_pass_through_unchanged(harness: Any) -> None:
    headers = harness.agent(["events.write"])
    rejected = {
        "error": {
            "code": "EVENT_TOO_LARGE",
            "message": "1 of 2 events were rejected; none were stored.",
            "details": {
                "rejected": [{"index": 1, "clientEventId": "b", "code": "EVENT_TOO_LARGE"}]
            },
        }
    }
    original = harness.app.state.broker.control._http._transport

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/event-batches"):
            return httpx.Response(422, json=rejected)
        return httpx.Response(200, json=harness.run)

    harness.app.state.broker.control._http._transport = httpx.MockTransport(handler)
    try:
        resp = await harness.client.post(
            f"{SDK}/events", headers=headers, json={"events": [_log("a"), _log("b")]}
        )
    finally:
        harness.app.state.broker.control._http._transport = original
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "EVENT_TOO_LARGE"
    assert resp.json()["error"]["details"] == rejected["error"]["details"]


async def test_claim_forwards_the_claim_token(harness: Any) -> None:
    headers = harness.agent(["idempotency"])
    ok = await harness.client.post(
        f"{SDK}/actions/row-1/claim", headers={**headers, "X-Claim-Token": "0123456789abcdef"}
    )
    assert ok.status_code == 200, ok.text
    assert harness.control_requests[-1]["body"] == {"attempt": 1, "claimToken": "0123456789abcdef"}
    plain = await harness.client.post(f"{SDK}/actions/row-2/claim", headers=headers)
    assert plain.status_code == 200 and harness.control_requests[-1]["body"] == {"attempt": 1}
    bad = await harness.client.post(
        f"{SDK}/actions/row-3/claim", headers={**headers, "X-Claim-Token": "no spaces!"}
    )
    assert bad.status_code == 422 and len(harness.control_requests) == 2


async def test_heartbeat_and_result_reach_a_finished_run(harness: Any) -> None:
    headers = harness.agent(["events.write", "idempotency"], state="FAILED")
    beat = await harness.client.post(f"{SDK}/heartbeat", headers=headers)
    assert beat.status_code == 200, beat.text
    assert harness.control_requests[-1]["path"].endswith("/heartbeat")
    result = await harness.client.post(
        f"{SDK}/result", headers=headers, json={"status": "failed", "error": None}
    )
    assert result.status_code == 200, result.text
    for path in ("/actions/k/claim", "/events"):
        refused = await harness.client.post(
            SDK + path, headers=headers, json={"events": [_log("a")]}
        )
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "RUN_NOT_ACTIVE"
    # A replaced token is still refused, even for a heartbeat.
    harness.run["capabilityTokenId"] = "another"
    assert (await harness.client.post(f"{SDK}/heartbeat", headers=headers)).status_code == 401


class LoseFirstResponse(httpx.AsyncBaseTransport):
    """Delivers requests to the broker but drops the response of the first matching one,
    as a network failure after the broker acted would."""

    def __init__(self, app: Any, suffix: str) -> None:
        self.inner = httpx.ASGITransport(app=app)
        self.suffix = suffix
        self.dropped = 0
        self.tokens: list[str | None] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        if request.url.path.endswith(self.suffix):
            self.tokens.append(request.headers.get("x-claim-token"))
            if not self.dropped:
                self.dropped += 1
                await response.aread()
                raise httpx.ReadError("connection reset", request=request)
        return response


async def _running_agent(
    owner: httpx.AsyncClient, platform: Any, broker_settings: BrokerSettings, app: Any
) -> tuple[str, str, Any]:
    specs: list[RunSpec] = []
    start = platform.runtime.start_run

    async def capture(spec: RunSpec) -> str:
        specs.append(spec)
        return str(await start(spec))

    platform.runtime.start_run = capture
    install = await owner.post("/api/v1/agent-installations", json=HELLO)
    assert install.status_code == 201, install.text
    run_id = (
        await owner.post("/api/v1/runs", json={"installationId": install.json()["id"]})
    ).json()["id"]
    for _ in range(200):
        if (await owner.get(f"/api/v1/runs/{run_id}")).json()["state"] == "RUNNING":
            break
        await asyncio.sleep(0.05)
    broker = create_app(broker_settings, control_transport=httpx.ASGITransport(app=app))
    return run_id, specs[0].env["PLATFORM_RUN_TOKEN"], broker


async def test_sdk_salvage_after_a_rejected_batch_stores_each_event_once(
    owner: httpx.AsyncClient,
    app: Any,
    platform: Any,
    catalog_synced: None,
    broker_settings: BrokerSettings,
    harness: Any,
) -> None:
    run_id, token, broker = await _running_agent(owner, platform, broker_settings, app)
    history = f"/api/v1/runs/{run_id}/events/history"
    before = {e["sequence"] for e in (await owner.get(history)).json()}
    async with broker.router.lifespan_context(broker):
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=broker))
        transport = BrokerClient("http://broker", token, http=http, sleep=_no_sleep)
        events = EventsClient(transport, echo=None)
        await events.log("info", "first")
        await events.metric("oversized", 1.0)
        # Past the 16 KiB limit after the SDK's own truncation: a raw oversized payload.
        events._buffer[-1]["payload"] = {"name": "m", "value": 1, "unit": "x" * 17_000}
        await events.log("info", "last")
        await events.flush()
        await transport.aclose()
    stored = [e for e in (await owner.get(history)).json() if e["sequence"] not in before]
    messages = [e["payload"].get("message") for e in stored if e["type"] == "run.log"]
    assert messages == ["first", "last"]  # each valid event exactly once
    assert not [e for e in stored if e["type"] == "run.metric"]


async def test_sdk_claim_retry_after_a_lost_response_is_still_claimed(
    owner: httpx.AsyncClient,
    app: Any,
    platform: Any,
    catalog_synced: None,
    broker_settings: BrokerSettings,
    harness: Any,
) -> None:
    run_id, token, broker = await _running_agent(owner, platform, broker_settings, app)
    lossy = LoseFirstResponse(broker, "/claim")
    async with broker.router.lifespan_context(broker):
        transport = BrokerClient(
            "http://broker", token, http=httpx.AsyncClient(transport=lossy), sleep=_no_sleep
        )
        actions = IdempotencyClient(transport)
        calls: list[str] = []

        async def side_effect() -> dict[str, str]:
            calls.append("dialed")
            return {"sid": "CA1"}

        result = await actions.once("call:row-9", side_effect)
        assert result == {"sid": "CA1"} and calls == ["dialed"]
        assert lossy.dropped == 1
        assert len(lossy.tokens) == 2 and lossy.tokens[0] == lossy.tokens[1]
        assert lossy.tokens[0] is not None
        # A separate claim() call is a different claimant: the key is now completed.
        again = await actions.claim("call:row-9")
        assert again.status == "completed" and again.result == {"sid": "CA1"}
        await transport.aclose()
    assert run_id


async def _no_sleep(_: float) -> None:
    return None
