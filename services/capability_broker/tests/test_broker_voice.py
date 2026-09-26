"""Realtime voice calls: telephone audio helpers, call placement, and the full loop with the
test playing Twilio's part against a real broker and a real model gateway (in-process mock
models): greeting -> caller speech -> transcription -> local chat -> streamed speech,
barge-in, hang-up, lease release, and the stream's authentication."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import socket
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import numpy as np
import pytest
import uvicorn
import websockets
from gateway_helpers import ASR_TTS_INSTALL, install_models
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_broker import fakes
from crewquarters_broker.config import BrokerSettings
from crewquarters_broker.main import create_app
from crewquarters_broker.voice import (
    MEDIA_PATH,
    VoiceActivityDetector,
    speakable,
    split_words,
    stream_twiml,
)
from crewquarters_broker.voice_audio import (
    FRAME_SAMPLES,
    Downsampler,
    ulaw_decode,
    ulaw_encode,
    wav_bytes,
)
from crewquarters_gateway.main import Gateway
from crewquarters_gateway.main import create_app as create_gateway_app
from crewquarters_shared.db.models import AuditEvent
from crewquarters_shared.db.models_gateway import ModelLease

SID = "AC" + "a" * 32
FROM = "+15550100"
TO = "+15550199"
PUBLIC = "https://demo.example.com"


# --- Audio helpers ---------------------------------------------------------------------------


def test_ulaw_reference_values_and_round_trip() -> None:
    # G.711 reference points: 0xFF is silence, 0x00/0x80 are the extremes.
    assert ulaw_decode(bytes([0xFF, 0x7F, 0x00, 0x80])).tolist() == [0, 0, -32124, 32124]
    samples = np.array([0, 100, -100, 1000, -1000, 12000, -12000, 32767, -32768], dtype=np.int16)
    decoded = ulaw_decode(ulaw_encode(samples)).astype(int)
    wide = samples.astype(int)  # abs(-32768) overflows int16
    assert np.all(np.abs(decoded - wide) <= np.maximum(8, np.abs(wide) // 16))
    assert (
        ulaw_decode(ulaw_encode(ulaw_decode(bytes(range(256))))).tolist()
        == ulaw_decode(bytes(range(256))).tolist()
    )


def test_downsampler_is_continuous_across_chunks() -> None:
    t = np.arange(24000) / 24000
    tone = (8000 * np.sin(2 * np.pi * 440 * t)).astype("<i2").tobytes()
    whole = Downsampler().feed(tone)
    stream = Downsampler()
    parts = np.concatenate([stream.feed(tone[i : i + 1918]) for i in range(0, len(tone), 1918)])
    assert len(whole) == len(parts) == 8000
    assert np.array_equal(whole, parts)
    assert 7500 < np.abs(whole[200:]).max() < 8500  # 440 Hz passes; the level is kept


def _tone(ms: int, level: int = 6000) -> np.ndarray:
    t = np.arange(8 * ms) / 8000
    return (level * np.sin(2 * np.pi * 300 * t)).astype(np.int16)


def _silence(ms: int) -> np.ndarray:
    return np.zeros(8 * ms, dtype=np.int16)


def _segments(samples: np.ndarray) -> list[tuple[str, int]]:
    vad, out = VoiceActivityDetector(), []
    for i in range(0, len(samples), FRAME_SAMPLES):
        kind, utterance = vad.feed(samples[i : i + FRAME_SAMPLES])
        if kind:
            out.append((kind, 0 if utterance is None else len(utterance) * 1000 // 8000))
    return out


def test_voice_activity_detection() -> None:
    audio = np.concatenate([_silence(500), _tone(1000), _silence(900)])
    [start, (end, ms)] = _segments(audio)
    assert start == ("start", 0) and end == "end" and 1000 <= ms <= 1800
    # A click shorter than the minimum utterance never counts as speech.
    assert [k for k, _ in _segments(np.concatenate([_tone(200), _silence(1000)]))] == ["start"]
    # Quiet background never starts speech.
    assert _segments(np.concatenate([_tone(2000, level=300), _silence(500)])) == []


def test_text_helpers() -> None:
    assert split_words("Hello the") == (["Hello"], "the")
    assert split_words("no space yet") == (["no", "space"], "yet")
    assert split_words("partial") == ([], "partial")
    assert speakable("**Sure!** here's `code` #1") == "Sure! here's code 1"
    twiml = stream_twiml("wss://x/media/1", 'tok"<&', "1")
    assert """<Parameter name="token" value='tok"&lt;&amp;'/>""" in twiml  # escaped, quoted
    assert twiml.index("<Say>") < twiml.index("<Connect>") < twiml.index("<Hangup/>")
    assert wav_bytes(_silence(20))[:4] == b"RIFF"


# --- Full loop --------------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def serve(app: Any) -> AsyncIterator[str]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 - uvicorn exposes only a flag
        await asyncio.sleep(0.01)
    try:
        yield f"127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


class Stack:
    def __init__(self, broker: str, twilio: fakes.FakeTwilio, settings: BrokerSettings) -> None:
        self.base = f"http://{broker}"
        self.ws = f"ws://{broker}"
        self.twilio = twilio
        token = settings.internal_service_token.get_secret_value()
        self.headers = {"Authorization": f"Bearer {token}"}
        self.http = httpx.AsyncClient(base_url=self.base, headers=self.headers, timeout=30)

    async def start(self, user_id: uuid.UUID, **body: Any) -> httpx.Response:
        return await self.http.post(
            "/internal/v1/voice/calls", json={"userId": str(user_id), **body}
        )

    async def call(self, call_id: str) -> dict[str, Any]:
        return (await self.http.get(f"/internal/v1/voice/calls/{call_id}")).json()


@pytest.fixture
async def stack(
    broker_settings: BrokerSettings,
    gateway: Gateway,
    person3_broker_tables: None,
) -> AsyncIterator[Stack]:
    await install_models(gateway, ASR_TTS_INSTALL)
    async with serve(create_gateway_app(gateway, background=False)) as gw:
        settings = broker_settings.model_copy(
            update={
                "model_gateway_url": f"http://{gw}",
                "public_base_url": PUBLIC,
                "voice_max_call_seconds": 60,
            }
        )
        twilio = fakes.FakeTwilio()
        app = create_app(settings, provider_transport=fakes.transport(fakes.FakeGoogle(), twilio))
        async with serve(app) as broker:
            stack = Stack(broker, twilio, settings)
            yield stack
            await stack.http.aclose()


def _media(samples: np.ndarray) -> list[str]:
    return [
        json.dumps(
            {
                "event": "media",
                "media": {"payload": base64.b64encode(ulaw_encode(samples[i : i + 160])).decode()},
            }
        )
        for i in range(0, len(samples), 160)
    ]


async def _until(ws: Any, event: str, limit: float = 20) -> tuple[dict[str, Any], int]:
    """Read Twilio-bound messages until ``event``; returns it and the audio bytes seen."""
    audio = 0
    async with asyncio.timeout(limit):
        while True:
            message = json.loads(await ws.recv())
            if message["event"] == "media":
                audio += len(base64.b64decode(message["media"]["payload"]))
            if message["event"] == event:
                return message, audio


async def _connect(stack: Stack, call: dict[str, Any], token: str | None = None) -> Any:
    ws = await websockets.connect(stack.ws + call["streamPath"])
    await ws.send(json.dumps({"event": "connected", "protocol": "Call"}))
    await ws.send(
        json.dumps(
            {
                "event": "start",
                "streamSid": "MZ1",
                "start": {
                    "streamSid": "MZ1",
                    "callSid": "CA1",
                    "customParameters": {"token": token or call["token"]},
                },
            }
        )
    )
    return ws


async def test_a_simulated_call_end_to_end(
    stack: Stack, user_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession]
) -> None:
    started = await stack.start(user_id, simulate=True, voice="male", instructions="Be brief.")
    assert started.status_code == 200, started.text
    call = started.json()
    assert call["state"] == "dialing" and call["streamPath"] == f"{MEDIA_PATH}/{call['id']}"
    busy = await stack.start(user_id, simulate=True)
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "VOICE_CALL_ACTIVE"

    ws = await _connect(stack, call)
    # The assistant greets first; its audio is 8 kHz mu-law followed by a playback mark.
    mark, audio = await _until(ws, "mark")
    assert audio > 0 and mark["streamSid"] == "MZ1"
    await ws.send(json.dumps({"event": "mark", "mark": {"name": "reply"}}))

    # The caller speaks for a second, then pauses: one turn.
    for message in _media(np.concatenate([_tone(1000), _silence(900)])):
        await ws.send(message)
    _, audio = await _until(ws, "mark")
    assert audio > 0
    view = await stack.call(call["id"])
    assert view["state"] == "connected" and view["voice"] == "male"
    roles = [t["role"] for t in view["turns"]]
    assert roles == ["assistant", "caller", "assistant"]
    caller, reply = view["turns"][1], view["turns"][2]
    assert caller["text"].startswith("Mock transcript of turn.wav")
    assert reply["text"].startswith("Mock reply to: Mock transcript")
    assert {"asrMs", "llmFirstTokenMs", "firstAudioMs"} <= set(reply["timings"])

    # Talking over the assistant (its mark not yet played) clears Twilio's audio buffer.
    for message in _media(_tone(500, level=9000)):
        await ws.send(message)
    cleared, _ = await _until(ws, "clear")
    assert cleared["streamSid"] == "MZ1"

    await ws.send(json.dumps({"event": "stop"}))
    await ws.wait_closed()
    for _ in range(100):
        view = await stack.call(call["id"])
        if view["state"] == "ended":
            break
        await asyncio.sleep(0.05)
    assert view["state"] == "ended" and view["endReason"] == "caller_hung_up"
    async with sessions() as db:
        leases = (
            await db.scalars(
                select(ModelLease).where(ModelLease.holder_id == f"voice:{call['id']}")
            )
        ).all()
        events = (
            await db.scalars(select(AuditEvent).where(AuditEvent.target_id == call["id"]))
        ).all()
    assert {le.model_id for le in leases} == {
        "local.asr.r2t2",
        "local.general.small",
        "local.tts.voxtream",
    }
    assert all(le.released_at is not None for le in leases)
    assert [e.action for e in events] == ["voice_call.simulated", "voice_call.ended"]
    assert "Mock" not in json.dumps([e.metadata_ for e in events])  # no transcript in audit
    # The session can start a new call once this one ended.
    assert (await stack.start(user_id, simulate=True)).status_code == 200


async def test_stream_authentication(stack: Stack, user_id: uuid.UUID) -> None:
    call = (await stack.start(user_id, simulate=True)).json()
    ws = await _connect(stack, call, token="wrong")
    await ws.wait_closed()
    assert ws.close_code == 4401
    with pytest.raises(websockets.InvalidStatus):  # unknown call: refused before accept
        await websockets.connect(stack.ws + f"{MEDIA_PATH}/{uuid.uuid4().hex}")
    # The real token still works once, and only once.
    ws = await _connect(stack, call)
    await _until(ws, "mark")
    with pytest.raises(websockets.InvalidStatus):
        await websockets.connect(stack.ws + call["streamPath"])
    await ws.send(json.dumps({"event": "stop"}))
    await ws.wait_closed()


async def test_live_call_placement(stack: Stack, user_id: uuid.UUID) -> None:
    unconfirmed = await stack.start(user_id, to=TO)
    assert unconfirmed.status_code == 422
    no_twilio = await stack.start(user_id, to=TO, confirm=True)
    assert no_twilio.status_code == 409 and no_twilio.json()["error"]["code"] == "NEEDS_CONNECTION"
    saved = await stack.http.put(
        "/internal/v1/connections/twilio",
        json={"userId": str(user_id), "accountSid": SID, "authToken": "t" * 32, "fromNumber": FROM},
    )
    assert saved.status_code == 200, saved.text
    placed = await stack.start(user_id, to=TO, confirm=True, voice="female")
    assert placed.status_code == 200, placed.text
    call = placed.json()
    assert call["to"] != TO and "streamPath" not in call and "token" not in call
    [sent] = stack.twilio.calls
    assert sent["To"] == TO and sent["From"] == FROM
    assert f'<Stream url="wss://demo.example.com{MEDIA_PATH}/{call["id"]}">' in sent["Twiml"]
    assert sent["StatusCallback"] == f"{PUBLIC}/api/v1/callbacks/twilio/voice-status/{call['id']}"
    # A live stream without Twilio's signature is refused before it is accepted.
    with pytest.raises(websockets.InvalidStatus):
        await websockets.connect(stack.ws + f"{MEDIA_PATH}/{call['id']}")
    ended = await stack.http.post(f"/internal/v1/voice/calls/{call['id']}/hangup")
    assert ended.status_code == 200 and ended.json()["state"] == "ended"
