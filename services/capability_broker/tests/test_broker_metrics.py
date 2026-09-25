"""Broker metrics: service-token only, templated routes, and nothing sensitive in labels."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

SDK = "/internal/v1/sdk"
METRICS = "/internal/v1/metrics"


def _value(text: str, prefix: str) -> float:
    lines = [line for line in text.splitlines() if line.startswith(prefix)]
    return sum(float(line.rsplit(" ", 1)[1]) for line in lines)


async def _metrics(h: Any) -> str:
    resp = await h.client.get(METRICS, headers=h.service_headers)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/plain")
    return resp.text


async def test_metrics_need_the_service_token(harness: Any) -> None:
    assert (await harness.client.get(METRICS)).status_code == 401
    bad = {"authorization": "Bearer nope"}
    assert (await harness.client.get(METRICS, headers=bad)).status_code == 401


async def test_requests_denials_and_provider_usage(harness: Any, user_id: uuid.UUID) -> None:
    await harness.connect_google(user_id)
    headers = harness.agent(["google.gmail.readonly"])
    assert (
        await harness.client.get(f"{SDK}/google/gmail/messages/m-plain", headers=headers)
    ).status_code == 200
    denied = await harness.client.post(
        f"{SDK}/knowledge/search", headers=headers, json={"knowledgeBaseId": "kb", "query": "q"}
    )
    assert denied.status_code == 403

    text = await _metrics(harness)
    assert _value(text, 'cq_broker_denials_total{code="CAPABILITY_DENIED"}') == 1
    assert _value(text, 'cq_broker_provider_requests_total{outcome="2xx",provider="google"}') >= 3
    assert 'route="/internal/v1/sdk/google/gmail/messages/{message_id}"' in text
    assert "m-plain" not in text  # the raw path never becomes a label
    assert headers["authorization"].removeprefix("Bearer ") not in text


async def test_oauth_refresh_failures_are_counted(harness: Any, user_id: uuid.UUID) -> None:
    await harness.connect_google(user_id)
    harness.google.revoked.update(harness.google.refresh_grants)
    harness.app.state.broker.google._access.clear()
    headers = harness.agent(["google.gmail.readonly"])
    assert (
        await harness.client.get(f"{SDK}/google/gmail/messages", headers=headers)
    ).status_code == 409
    text = await _metrics(harness)
    assert (
        _value(
            text, 'cq_broker_oauth_refresh_failures_total{provider="google",reason="invalid_grant"}'
        )
        == 1
    )


async def test_rejected_callbacks_and_transport_errors(
    live_harness: Any, user_id: uuid.UUID, real_run_id: uuid.UUID
) -> None:
    await live_harness.client.put(
        "/internal/v1/connections/twilio",
        headers=live_harness.service_headers,
        json={
            "userId": str(user_id),
            "accountSid": "AC" + "0" * 31 + "1",
            "authToken": "auth-token-value-0001",
            "fromNumber": "+15555550199",
        },
    )
    forged = await live_harness.client.post(
        f"/api/v1/callbacks/twilio/status/{uuid.uuid4()}",
        data={"CallSid": "CA1", "CallStatus": "completed"},
        headers={"x-twilio-signature": "forged"},
    )
    assert forged.status_code == 403

    headers = live_harness.agent(
        ["twilio.call.fixed_script"], run_id=real_run_id, config={"script": "Hi {name}."}
    )
    live_harness.twilio.fail_next = "timeout"
    body = {
        "to": "+15555550101",
        "script": {"disclosure": "d", "text": "Hi Asha."},
        "gather": {"input": "speech", "timeoutSeconds": 5},
        "idempotencyKey": "k1",
    }
    assert (
        await live_harness.client.post(f"{SDK}/telephony/calls", headers=headers, json=body)
    ).status_code == 503
    text = await _metrics(live_harness)
    assert _value(text, 'cq_broker_callback_rejections_total{provider="twilio"}') == 1
    assert _value(text, 'cq_broker_provider_requests_total{outcome="error",provider="twilio"}') == 1
    assert "+15555550101" not in text and "5555550101" not in text


@pytest.mark.no_db
async def test_metered_transport_passes_responses_through() -> None:
    from crewquarters_broker.metrics import BrokerMetrics, MeteredTransport

    metrics = BrokerMetrics()
    inner = httpx.MockTransport(lambda request: httpx.Response(418, text="teapot"))
    async with httpx.AsyncClient(transport=MeteredTransport(inner, metrics)) as client:
        resp = await client.get("https://api.twilio.com/x")
    assert resp.status_code == 418 and resp.text == "teapot"
    assert _value(metrics.render().decode(), "cq_broker_provider_requests_total") == 1
