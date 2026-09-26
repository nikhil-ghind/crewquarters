"""The real engine round trip: Kokoro speaks, Parakeet transcribes it back (marker `models`).

Needs `make speech-models` (or CREWQ_SPEECH_MODELS_DIR pointing at downloaded models) and the
`sherpa` extra (`uv sync --all-packages --all-extras`).
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import numpy as np
import pytest

from crewquarters_speech.catalog import STT_MODELS, TTS_MODELS
from crewquarters_speech.download import is_present
from crewquarters_speech.settings import SpeechSettings

pytestmark = pytest.mark.models
REPO = Path(__file__).resolve().parents[3]
MODELS = Path(os.environ.get("CREWQ_SPEECH_MODELS_DIR", REPO / ".models" / "speech"))


@pytest.fixture(scope="module")
def settings() -> SpeechSettings:
    pytest.importorskip("sherpa_onnx")
    base = SpeechSettings(engine="sherpa", models_dir=MODELS)
    for archive in (STT_MODELS[base.stt_model], TTS_MODELS[base.tts_model]):
        if not is_present(MODELS, archive):
            pytest.skip(f"{archive.name} not in {MODELS}; run `make speech-models`")
    return base


@pytest.fixture(scope="module")
def engine(settings: SpeechSettings):  # type: ignore[no-untyped-def]
    from crewquarters_speech.sherpa import SherpaSpeechEngine

    return SherpaSpeechEngine(settings)


def test_kokoro_speech_is_transcribed_back_by_parakeet(engine) -> None:  # type: ignore[no-untyped-def]
    text = "Hello from the platform, this is a test of the local speech server."
    started = time.perf_counter()
    chunks = list(engine.synthesize(text, "af_sarah", 1.0))
    synthesized = time.perf_counter() - started
    assert len(chunks) >= 2  # streamed clause by clause
    samples = np.frombuffer(b"".join(chunks), dtype="<i2").astype(np.float32) / 32768.0
    started = time.perf_counter()
    heard = engine.transcribe(samples, 24000).lower()
    transcribed = time.perf_counter() - started
    print(f"tts {synthesized:.2f}s for {len(samples) / 24000:.2f}s audio; stt {transcribed:.2f}s")
    assert "hello" in heard and "test" in heard and "speech" in heard


@pytest.fixture
async def api(settings: SpeechSettings, engine) -> AsyncIterator[httpx.AsyncClient]:  # type: ignore[no-untyped-def]
    from crewquarters_speech.app import create_app

    transport = httpx.ASGITransport(app=create_app(settings, engine=engine))
    async with httpx.AsyncClient(transport=transport, base_url="http://speech") as client:
        yield client


async def test_http_round_trip_with_real_models(api: httpx.AsyncClient) -> None:
    body = {
        "model": "local.tts.small",
        "input": "Yes, next Tuesday works for me.",
        "response_format": "wav",
        "voice": "am_adam",
    }
    speech = await api.post("/v1/audio/speech", json=body, timeout=60)
    assert speech.status_code == 200
    wav = speech.content
    # The streamed header has placeholder sizes; rewrite them so the WAV parser accepts it.
    fixed = (
        wav[:4]
        + (len(wav) - 8).to_bytes(4, "little")
        + wav[8:40]
        + (len(wav) - 44).to_bytes(4, "little")
        + wav[44:]
    )
    files = {"file": ("reply.wav", fixed, "audio/wav")}
    heard = await api.post(
        "/v1/audio/transcriptions", data={"model": "local.stt.small"}, files=files, timeout=60
    )
    assert "tuesday" in heard.json()["text"].lower()
