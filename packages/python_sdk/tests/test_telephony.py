import json
from typing import Any

import httpx
import pytest
from fakebroker import FakeBroker

from crewquarters._transport import BrokerClient
from crewquarters.errors import InvalidInput
from crewquarters.telephony import TelephonyClient, is_e164


def call(state: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": "CA1",
        "idempotencyKey": "call:1",
        "toMasked": "••••0101",
        "state": state,
        "answered": state in {"in-progress", "completed"},
        "speechCaptured": False,
        "transcript": None,
        "durationSeconds": None,
        "errorCode": None,
        "createdAt": "2026-09-24T10:00:00Z",
        "updatedAt": "2026-09-24T10:00:01Z",
        **extra,
    }


def test_is_e164() -> None:
    assert is_e164("+14155550123")
    assert is_e164("+918012345678")
    assert not is_e164("14155550123")
    assert not is_e164("+1 415 555 0123")
    assert not is_e164("+0123456789")
    assert not is_e164("+1234567")


async def test_create_call_sends_the_fixed_script() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=call("queued"))

    broker = FakeBroker()
    broker.overrides[("POST", "/telephony/calls")] = handler
    telephony = TelephonyClient(BrokerClient("http://broker.test", "t", http=broker.client()))
    created = await telephony.create_call(
        "+15555550101",
        disclosure="Automated.",
        script="Hello Asha",
        gather_seconds=20,
        idempotency_key="call:1",
    )
    assert created.state == "queued"
    assert created.to_masked == "••••0101"
    assert seen[0] == {
        "to": "+15555550101",
        "script": {"disclosure": "Automated.", "text": "Hello Asha"},
        "gather": {"input": "speech", "timeoutSeconds": 20},
        "idempotencyKey": "call:1",
    }


async def test_invalid_number_is_rejected_before_any_request() -> None:
    broker = FakeBroker()
    telephony = TelephonyClient(BrokerClient("http://broker.test", "t", http=broker.client()))
    with pytest.raises(InvalidInput) as info:
        await telephony.create_call(
            "5550101", disclosure="d", script="s", gather_seconds=10, idempotency_key="k"
        )
    assert "5550101" not in str(info.value)
    assert broker.requests == []


async def test_wait_for_call_polls_until_terminal() -> None:
    states = iter(["ringing", "in-progress", "completed"])
    broker = FakeBroker()
    broker.overrides[("GET", "/telephony/calls/CA1")] = lambda r: httpx.Response(
        200, json=call(next(states))
    )
    telephony = TelephonyClient(BrokerClient("http://broker.test", "t", http=broker.client()))
    final = await telephony.wait_for_call("CA1", timeout_seconds=5, poll_seconds=0)
    assert final.state == "completed"
    assert final.terminal


async def test_wait_for_call_returns_last_state_at_timeout() -> None:
    broker = FakeBroker()
    broker.overrides[("GET", "/telephony/calls/CA1")] = lambda r: httpx.Response(
        200, json=call("ringing")
    )
    telephony = TelephonyClient(BrokerClient("http://broker.test", "t", http=broker.client()))
    final = await telephony.wait_for_call("CA1", timeout_seconds=0.05, poll_seconds=0.01)
    assert final.state == "ringing"
    assert not final.terminal
