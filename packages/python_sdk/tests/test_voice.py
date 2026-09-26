from __future__ import annotations

import httpx
import pytest
from fakebroker import FakeBroker, error

from crewquarters._transport import BrokerClient
from crewquarters.context import ModelEndpoint
from crewquarters.errors import InvalidInput
from crewquarters.voice import VoiceClient


async def no_sleep(_: float) -> None:
    return None


def make(broker: FakeBroker) -> VoiceClient:
    transport = BrokerClient(
        "http://broker.test", "run-token", http=broker.client(), sleep=no_sleep
    )
    return VoiceClient(transport)


async def test_dial_returns_the_call_and_room() -> None:
    broker = FakeBroker()
    call = await make(broker).dial(
        "+15555550101",
        idempotency_key="voice:run:2",
        ring_timeout_seconds=20,
        max_duration_seconds=240,
    )
    assert broker.voice_bodies == [
        {
            "to": "+15555550101",
            "idempotencyKey": "voice:run:2",
            "ringTimeoutSeconds": 20,
            "maxDurationSeconds": 240,
        }
    ]
    assert call.to_masked == "••••0101" and call.state == "ringing" and not call.ended
    assert call.room is not None
    assert (call.room.url, call.room.token, call.room.identity) == (
        "ws://livekit.test:7880",
        "room-token",
        "agent",
    )


async def test_invalid_number_is_rejected_without_a_request() -> None:
    broker = FakeBroker()
    with pytest.raises(InvalidInput) as info:
        await make(broker).dial("5550101", idempotency_key="k")
    assert "5550101" not in str(info.value)
    assert broker.requests == []


async def test_dial_is_retried_after_a_503_because_it_carries_its_key() -> None:
    broker = FakeBroker()
    attempts = 0

    def flaky(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return error(503, "PROVIDER_UNAVAILABLE")
        broker.overrides.clear()
        return broker.handler(request)

    broker.overrides[("POST", "/voice/calls")] = flaky
    call = await make(broker).dial("+15555550101", idempotency_key="k")
    assert attempts == 2 and call.id == "vc-1"


async def test_get_and_hangup() -> None:
    broker = FakeBroker()
    client = make(broker)
    call = await client.dial("+15555550101", idempotency_key="k")
    assert (await client.get(call.id)).state == "ringing"
    ended = await client.hangup(call.id)
    assert ended.state == "completed" and ended.ended and ended.room is None


def test_model_endpoint_points_at_the_broker_facade_with_the_run_token() -> None:
    transport = BrokerClient("http://broker.test/", "run-token")
    endpoint = ModelEndpoint.for_transport(transport)
    assert endpoint == ModelEndpoint(
        base_url="http://broker.test/internal/v1/sdk/openai/v1", api_key="run-token"
    )
