"""Text-to-speech models: buffered speech for the owner, the streaming WebSocket proxy
(text in, PCM out, cancel), voice validation, and the voice-call credential's scope."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import socket
import wave
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
import websockets
from gateway_helpers import CHAT_TOKEN, TOKEN, chat_body, install
from sqlalchemy import select

from crewquarters_gateway.adapters import MOCK_FRAME_SAMPLES, mock_speech_pcm
from crewquarters_gateway.main import Gateway, create_app
from crewquarters_shared.db.models_gateway import ModelLease

TTS = "local.tts.voxtream"
ASR = "local.asr.r2t2"
VOICE_TOKEN = "dev-insecure-voice-token-change-me-00000000"
# The shared test client also sends the chat token; blank it so only the voice token counts.
VOICE = {
    "Authorization": f"Bearer {TOKEN}",
    "X-Voice-Client-Token": VOICE_TOKEN,
    "X-Chat-Client-Token": "",
}
OWNER = {"Authorization": f"Bearer {TOKEN}", "X-Chat-Client-Token": CHAT_TOKEN}
MOCK_SERVER = Path(__file__).resolve().parents[3] / "catalog/models/dev/files/mock_openai_server.py"


async def test_owner_speech_returns_a_wav_and_releases_its_lease(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client, TTS)
    response = await gw_client.post(
        "/internal/v1/audio/speech",
        json={"modelId": TTS, "input": "hello crew", "voice": "male"},
        headers={"X-Actor-Id": "owner-1"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(response.content)) as w:
        assert w.getframerate() == 24000 and w.getnframes() == 2 * MOCK_FRAME_SAMPLES
    assert float(response.headers["x-audio-seconds"]) == pytest.approx(0.16)
    async with gateway.sessions() as db:
        leases = (await db.scalars(select(ModelLease).where(ModelLease.model_id == TTS))).all()
    assert [(le.holder_type, le.released_at is not None) for le in leases] == [("manual", True)]


async def test_speech_input_voice_and_capability_checks(gw_client: httpx.AsyncClient) -> None:
    await install(gw_client, TTS)
    await install(gw_client, ASR)
    url = "/internal/v1/audio/speech"
    empty = await gw_client.post(url, json={"modelId": TTS, "input": " "})
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "INVALID_INPUT"
    long = await gw_client.post(url, json={"modelId": TTS, "input": "x" * 1001})
    assert long.status_code == 422
    voice = await gw_client.post(url, json={"modelId": TTS, "input": "hi", "voice": "someone"})
    assert voice.status_code == 422 and voice.json()["error"]["code"] == "UNKNOWN_VOICE"
    assert voice.json()["error"]["details"]["voices"] == ["female", "male"]
    wrong = await gw_client.post(url, json={"modelId": ASR, "input": "hi"})
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "MODEL_CAPABILITY_UNSUPPORTED"
    chat = await gw_client.post("/internal/v1/llm/chat", json=chat_body(profile=TTS))
    assert chat.status_code == 422


@contextlib.asynccontextmanager
async def serve(gateway: Gateway) -> AsyncIterator[str]:
    """The gateway app on a real server in this event loop (its model worker shares it)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(
        create_app(gateway, background=False), host="127.0.0.1", port=port, log_level="error"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 - uvicorn exposes only a flag
        await asyncio.sleep(0.01)
    try:
        yield f"ws://127.0.0.1:{port}/internal/v1/audio/speech/stream"
    finally:
        server.should_exit = True
        await task


async def _speak(url: str, headers: dict[str, str], messages: list[dict[str, Any]], **params: str):  # type: ignore[no-untyped-def]
    query = "&".join(f"{k}={v}" for k, v in {"modelId": TTS, **params}.items())
    async with websockets.connect(f"{url}?{query}", additional_headers=headers) as ws:
        for message in messages:
            await ws.send(json.dumps(message))
        pcm = b""
        async for message in ws:
            if isinstance(message, bytes):
                pcm += message
            else:
                return pcm, json.loads(message)
    raise AssertionError("closed without a final event")


async def test_streaming_speech_through_the_gateway(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client, TTS)
    words = [{"type": "text", "text": w} for w in ("hello", "there", "crew")]
    async with serve(gateway) as url:
        pcm, done = await _speak(
            url, VOICE, [*words, {"type": "end"}], voice="female", holderId="call-1"
        )
        assert pcm == mock_speech_pcm("hello there crew"), done
        assert done["type"] == "done" and done["cancelled"] is False
        pcm, done = await _speak(
            url,
            VOICE,
            [{"type": "text", "text": "never spoken"}, {"type": "cancel"}],
            holderId="call-1",
        )
        assert pcm == b"" and done["cancelled"] is True
        _, error = await _speak(url, VOICE, [], voice="nobody", holderId="call-1")
        assert error["type"] == "error" and error["error"]["code"] == "UNKNOWN_VOICE"
    # A voice call keeps one renewable lease per model until it releases it.
    async with gateway.sessions() as db:
        leases = (await db.scalars(select(ModelLease).where(ModelLease.model_id == TTS))).all()
    assert {(le.holder_type, le.holder_id, le.released_at is None) for le in leases} == {
        ("manual", "voice:call-1", True)
    }


async def test_streaming_speech_requires_both_credentials(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    await install(gw_client, TTS)
    async with serve(gateway) as url:
        for headers in (
            {"Authorization": f"Bearer {TOKEN}"},
            {"X-Voice-Client-Token": VOICE_TOKEN},
        ):
            with pytest.raises(websockets.InvalidStatus) as refused:
                await _speak(url, headers, [])
            assert refused.value.response.status_code == 403
        _, error = await _speak(url, VOICE, [])  # voice calls must name their call
        assert error["error"]["code"] == "HOLDER_REQUIRED"


async def test_voice_credential_scope(gw_client: httpx.AsyncClient, gateway: Gateway) -> None:
    await install(gw_client)
    await install(gw_client, ASR)
    # Local chat for a call, held by the call's lease.
    body = chat_body(holder={"type": "voice", "id": "call-9"})
    chat = await gw_client.post("/internal/v1/llm/chat", json=body, headers=VOICE)
    assert chat.status_code == 200, chat.text
    # Cloud is never available to a call.
    cloud = await gw_client.post(
        "/internal/v1/llm/chat", json={**body, "profile": "openai.default"}, headers=VOICE
    )
    assert cloud.status_code == 403
    # The voice token cannot impersonate an owner chat session...
    chat_holder = await gw_client.post("/internal/v1/llm/chat", json=chat_body(), headers=VOICE)
    assert chat_holder.status_code == 401
    # ...and releases only voice-call leases.
    other = await gw_client.delete("/internal/v1/leases/holders/chat/session-1", headers=VOICE)
    assert other.status_code == 403
    mine = await gw_client.delete("/internal/v1/leases/holders/manual/voice:call-9", headers=VOICE)
    assert mine.status_code == 200 and mine.json() == {"released": 1}
    # Transcription for a call needs the call id.
    missing = await gw_client.post(
        "/internal/v1/audio/transcriptions",
        params={"modelId": ASR},
        content=b"RIFF" + bytes(64),
        headers={**VOICE, "Content-Type": "audio/wav"},
    )
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "HOLDER_REQUIRED"
    ok = await gw_client.post(
        "/internal/v1/audio/transcriptions",
        params={"modelId": ASR, "holderId": "call-9"},
        content=b"RIFF" + bytes(64),
        headers={**VOICE, "Content-Type": "audio/wav"},
    )
    assert ok.status_code == 200
    async with gateway.sessions() as db:
        asr = (await db.scalars(select(ModelLease).where(ModelLease.model_id == ASR))).one()
    assert asr.holder_id == "voice:call-9" and asr.released_at is None


def test_mock_server_speech_matches_the_in_process_mock() -> None:
    spec = importlib.util.spec_from_file_location("mock_openai_server", MOCK_SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for text in ("hello crew", "", "one two three four"):
        assert module.mock_speech_pcm(text) == mock_speech_pcm(text)


def test_every_dgx_profile_meets_the_readiness_contract() -> None:
    """The gateway marks a model ready only when GET <healthPath> lists the served model name
    (vLLM's /v1/models shape), and the daemon passes that name only through
    {served_model_name}. A server that answers only {"status": "ok"} never becomes ready."""
    catalog = Path(__file__).resolve().parents[3] / "catalog/models/dgx"
    for path in sorted(catalog.glob("*.json")):
        launch = json.loads(path.read_text())["launch"]
        assert launch.get("healthPath", "/v1/models") == "/v1/models", path.name
        assert "{served_model_name}" in launch["args"], path.name
