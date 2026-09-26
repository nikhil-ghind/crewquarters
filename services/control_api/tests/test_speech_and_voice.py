"""Owner text-to-speech through the real model gateway, and realtime voice-call routes
through the real capability broker (fake providers)."""

from __future__ import annotations

import io
import wave
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from gateway_helpers import CHAT_TOKEN, TOKEN, install
from sqlalchemy import select

from crewquarters_api.gateway_client import GatewayClient
from crewquarters_api.main import create_app
from crewquarters_broker.config import BrokerSettings
from crewquarters_broker.main import create_app as create_broker
from crewquarters_gateway.adapters import MOCK_FRAME_SAMPLES
from crewquarters_gateway.main import Gateway
from crewquarters_gateway.main import create_app as create_gateway_app
from crewquarters_shared.config import Settings
from crewquarters_shared.db.models import AuditEvent

TTS = "local.tts.voxtream"


@pytest.fixture
async def gw_app(gateway: Gateway) -> Any:
    return create_gateway_app(gateway, background=False)


@pytest.fixture
async def app(settings: Settings, gw_app: Any) -> AsyncIterator[Any]:
    broker = create_broker(
        BrokerSettings(**{**settings.model_dump(), "provider_mode": "fake"}),
    )
    application = create_app(
        settings.model_copy(update={"broker_adapter": "http"}),
        transports={"broker": httpx.ASGITransport(app=broker)},
    )
    application.state.cq.models = GatewayClient(
        "http://gateway", TOKEN, transport=httpx.ASGITransport(app=gw_app), chat_token=CHAT_TOKEN
    )
    yield application
    await application.state.engine.dispose()


@pytest.fixture
async def gw(gw_app: Any) -> AsyncIterator[httpx.AsyncClient]:
    headers = {"Authorization": f"Bearer {TOKEN}", "X-Chat-Client-Token": CHAT_TOKEN}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gw_app), base_url="http://gateway", headers=headers
    ) as client:
        yield client


async def test_owner_speech_returns_a_wav(
    owner: httpx.AsyncClient, gw: httpx.AsyncClient, sessions: Any
) -> None:
    missing = await owner.post(f"/api/v1/models/{TTS}/speech", json={"text": "hello crew"})
    assert missing.status_code == 409
    await install(gw, TTS)
    response = await owner.post(
        f"/api/v1/models/{TTS}/speech", json={"text": "hello crew", "voice": "male"}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["x-audio-seconds"] == "0.16"
    with wave.open(io.BytesIO(response.content)) as w:
        assert w.getnframes() == 2 * MOCK_FRAME_SAMPLES
    voice = await owner.post(f"/api/v1/models/{TTS}/speech", json={"text": "hi", "voice": "x"})
    assert voice.status_code == 422 and voice.json()["error"]["code"] == "UNKNOWN_VOICE"
    too_long = await owner.post(f"/api/v1/models/{TTS}/speech", json={"text": "x" * 1001})
    assert too_long.status_code == 422
    async with sessions() as db:
        events = (
            await db.scalars(select(AuditEvent).where(AuditEvent.action == "model.speak"))
        ).all()
    assert [(e.outcome, e.metadata_) for e in events] == [
        ("failure", {"characters": 10, "voice": "female"}),
        ("success", {"characters": 10, "voice": "male"}),
        ("failure", {"characters": 2, "voice": "x"}),
    ]


async def test_voice_call_routes(owner: httpx.AsyncClient) -> None:
    body = {"to": "+15555550101", "confirm": True}
    no_twilio = await owner.post("/api/v1/connections/twilio/voice-calls", json=body)
    assert no_twilio.status_code == 409
    assert no_twilio.json()["error"]["code"] == "NEEDS_CONNECTION"
    unconfirmed = await owner.post(
        "/api/v1/connections/twilio/voice-calls", json={**body, "confirm": False}
    )
    assert unconfirmed.status_code == 422
    bad_number = await owner.post(
        "/api/v1/connections/twilio/voice-calls", json={**body, "to": "5550101"}
    )
    assert bad_number.status_code == 422
    unknown = await owner.get("/api/v1/connections/twilio/voice-calls/" + "a" * 32)
    assert unknown.status_code == 404
    malformed = await owner.post("/api/v1/connections/twilio/voice-calls/../x/hangup")
    assert malformed.status_code == 404
