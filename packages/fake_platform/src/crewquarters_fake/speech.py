"""Speech models for the fake platform's model gateway.

``LocalSpeech`` runs the deterministic fake engine in process: a simulated callee queues what it
is about to say, and the agent's speech-to-text "hears" exactly that. ``RemoteSpeech`` forwards to
a real speech server (``CREWQ_FAKE_SPEECH_URL``, for example ``make voice-up``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

import httpx
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from crewquarters_fake.errors import ApiError
from crewquarters_speech.audio import OUTPUT_SAMPLE_RATE, InvalidAudio, decode_wav, wav_header
from crewquarters_speech.fake import FakeSpeechEngine

MEDIA_TYPES = {"pcm": f"audio/pcm; rate={OUTPUT_SAMPLE_RATE}", "wav": "audio/wav"}


class SpeechService(Protocol):
    async def transcribe(
        self, audio: bytes, model: str, language: str | None, channel: str = ""
    ) -> str: ...

    def synthesize(
        self, model: str, text: str, voice: str | None, response_format: str, speed: float
    ) -> AsyncIterator[bytes]: ...


def media_type(response_format: str) -> str:
    if response_format not in MEDIA_TYPES:
        raise ApiError(
            422, "UNSUPPORTED_FORMAT", f"response_format must be one of {sorted(MEDIA_TYPES)}"
        )
    return MEDIA_TYPES[response_format]


class LocalSpeech:
    def __init__(self, engine: FakeSpeechEngine) -> None:
        self.engine = engine

    async def transcribe(
        self, audio: bytes, model: str, language: str | None, channel: str = ""
    ) -> str:
        try:
            samples, rate = decode_wav(audio)
        except InvalidAudio as exc:
            raise ApiError(422, "INVALID_AUDIO", str(exc)) from exc
        return await run_in_threadpool(self.engine.transcribe, samples, rate, channel)

    async def synthesize(
        self, model: str, text: str, voice: str | None, response_format: str, speed: float
    ) -> AsyncIterator[bytes]:
        if response_format == "wav":
            yield wav_header(OUTPUT_SAMPLE_RATE)
        async for chunk in iterate_in_threadpool(
            self.engine.synthesize(text, voice or "af_sarah", speed)
        ):
            yield chunk


class RemoteSpeech:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def transcribe(
        self, audio: bytes, model: str, language: str | None, channel: str = ""
    ) -> str:
        data = {"model": model, **({"language": language} if language else {})}
        files = {"file": ("utterance.wav", audio, "audio/wav")}
        try:
            async with httpx.AsyncClient(timeout=60) as http:
                response = await http.post(
                    f"{self.base_url}/v1/audio/transcriptions", data=data, files=files
                )
        except httpx.HTTPError as exc:
            raise ApiError(503, "MODEL_UNAVAILABLE", f"speech server unreachable: {exc}") from exc
        _raise_for(response)
        return str(response.json().get("text", ""))

    async def synthesize(
        self, model: str, text: str, voice: str | None, response_format: str, speed: float
    ) -> AsyncIterator[bytes]:
        body = {"model": model, "input": text, "response_format": response_format, "speed": speed}
        if voice:
            body["voice"] = voice
        try:
            async with (
                httpx.AsyncClient(timeout=httpx.Timeout(60, read=120)) as http,
                http.stream("POST", f"{self.base_url}/v1/audio/speech", json=body) as response,
            ):
                if response.status_code >= 400:
                    await response.aread()
                    _raise_for(response)
                async for chunk in response.aiter_bytes():
                    yield chunk
        except httpx.HTTPError as exc:
            raise ApiError(503, "MODEL_UNAVAILABLE", f"speech server unreachable: {exc}") from exc


def _raise_for(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    try:
        error = response.json().get("error", {})
    except ValueError:
        error = {}
    status = response.status_code if response.status_code < 500 else 502
    raise ApiError(
        status,
        str(error.get("code") or "PROVIDER_ERROR"),
        str(error.get("message") or f"speech server returned HTTP {response.status_code}"),
    )
