"""The speech server's OpenAI-compatible HTTP API, exercised with the deterministic fake engine."""

from __future__ import annotations

import io
import wave
from collections.abc import AsyncIterator

import httpx
import numpy as np
import pytest

from crewquarters_speech.app import create_app
from crewquarters_speech.fake import FakeSpeechEngine
from crewquarters_speech.settings import SpeechSettings

STT = "local.stt.small"
TTS = "local.tts.small"


def wav_bytes(seconds: float = 0.5, rate: int = 16000) -> bytes:
    samples = (np.sin(np.arange(int(seconds * rate)) * 0.05) * 8000).astype("<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return out.getvalue()


@pytest.fixture
def engine() -> FakeSpeechEngine:
    return FakeSpeechEngine()


@pytest.fixture
async def api(engine: FakeSpeechEngine) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(SpeechSettings(engine="fake"), engine=engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://speech") as client:
        yield client


async def transcribe(api: httpx.AsyncClient, **form: str) -> httpx.Response:
    files = {"file": ("utterance.wav", wav_bytes(), "audio/wav")}
    return await api.post("/v1/audio/transcriptions", data={"model": STT, **form}, files=files)


async def test_transcription_returns_queued_lines_in_order(
    api: httpx.AsyncClient, engine: FakeSpeechEngine
) -> None:
    engine.queue_transcripts(["Hello?", "Sure, go ahead."])
    assert (await transcribe(api)).json() == {"text": "Hello?"}
    assert (await transcribe(api)).json() == {"text": "Sure, go ahead."}
    assert (await transcribe(api)).json() == {"text": ""}


async def test_transcription_text_format(api: httpx.AsyncClient, engine: FakeSpeechEngine) -> None:
    engine.queue_transcripts(["Yes."])
    response = await transcribe(api, response_format="text")
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "Yes."


async def test_speech_streams_pcm_that_grows_with_the_text(api: httpx.AsyncClient) -> None:
    body = {"model": TTS, "input": "Hi there.", "response_format": "pcm", "voice": "af_sarah"}
    short = await api.post("/v1/audio/speech", json=body)
    long = await api.post(
        "/v1/audio/speech",
        json={**body, "input": "Hi there, this is a much longer sentence with many more words."},
    )
    assert short.status_code == 200
    assert short.headers["content-type"].startswith("audio/pcm")
    assert len(short.content) % 2 == 0
    assert 0 < len(short.content) < len(long.content)


async def test_speech_accepts_openai_client_extras(api: httpx.AsyncClient) -> None:
    body = {"model": TTS, "input": "Hello.", "response_format": "pcm", "stream_format": "sse"}
    assert (await api.post("/v1/audio/speech", json=body)).status_code == 200


async def test_speech_wav_has_a_riff_header(api: httpx.AsyncClient) -> None:
    body = {"model": TTS, "input": "Hello there.", "response_format": "wav"}
    response = await api.post("/v1/audio/speech", json=body)
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content[:4] == b"RIFF" and response.content[8:12] == b"WAVE"


@pytest.mark.parametrize("path", ["/v1/audio/speech", "/v1/audio/transcriptions"])
async def test_unknown_model_is_not_found(api: httpx.AsyncClient, path: str) -> None:
    if path.endswith("speech"):
        response = await api.post(path, json={"model": "local.tts.huge", "input": "Hi."})
    else:
        files = {"file": ("u.wav", wav_bytes(), "audio/wav")}
        response = await api.post(path, data={"model": "local.stt.huge"}, files=files)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MODEL_NOT_FOUND"


async def test_empty_input_is_rejected(api: httpx.AsyncClient) -> None:
    response = await api.post("/v1/audio/speech", json={"model": TTS, "input": ""})
    assert response.status_code == 422


async def test_unsupported_audio_format_is_rejected(api: httpx.AsyncClient) -> None:
    body = {"model": TTS, "input": "Hi.", "response_format": "mp3"}
    response = await api.post("/v1/audio/speech", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSUPPORTED_FORMAT"


async def test_invalid_audio_is_rejected(api: httpx.AsyncClient) -> None:
    files = {"file": ("u.wav", b"not a wav file", "audio/wav")}
    response = await api.post("/v1/audio/transcriptions", data={"model": STT}, files=files)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_AUDIO"


async def test_models_and_readiness(api: httpx.AsyncClient) -> None:
    models = {m["id"] for m in (await api.get("/v1/models")).json()["data"]}
    assert models == {STT, TTS}
    assert (await api.get("/health/ready")).json() == {"status": "ready", "engine": "fake"}
