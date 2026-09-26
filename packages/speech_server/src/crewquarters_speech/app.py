"""OpenAI-compatible HTTP API: POST /v1/audio/transcriptions and POST /v1/audio/speech."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from crewquarters_speech import __version__
from crewquarters_speech.audio import OUTPUT_SAMPLE_RATE, InvalidAudio, decode_wav, wav_header
from crewquarters_speech.engines import SpeechEngine, load_engine
from crewquarters_speech.settings import SpeechSettings

MEDIA_TYPES = {"pcm": f"audio/pcm; rate={OUTPUT_SAMPLE_RATE}", "wav": "audio/wav"}


class SpeechError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class SpeechIn(BaseModel):
    # OpenAI clients also send stream_format, instructions, and similar; they are ignored.
    model_config = ConfigDict(extra="ignore")

    model: str
    input: str = Field(min_length=1, max_length=4000)
    voice: str | None = None
    response_format: str = "pcm"
    speed: float = Field(1.0, ge=0.5, le=2.0)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def create_app(settings: SpeechSettings, engine: SpeechEngine | None = None) -> FastAPI:
    app = FastAPI(title="Crewquarters speech server", version=__version__)
    speech = engine or load_engine(settings)

    @app.exception_handler(SpeechError)
    async def speech_error(_: Request, exc: SpeechError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(422, "INVALID_REQUEST", str(exc.errors()[:3]))

    def require(model: str, served: tuple[str, ...]) -> None:
        if model not in served:
            raise SpeechError(404, "MODEL_NOT_FOUND", f"{model} is not served here ({served})")

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ready", "engine": speech.name}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        served = [*settings.stt_profiles, *settings.tts_profiles]
        return {"object": "list", "data": [{"id": m, "object": "model"} for m in served]}

    @app.post("/v1/audio/transcriptions", response_model=None)
    async def transcriptions(
        file: UploadFile = File(...),
        model: str = Form(...),
        language: str | None = Form(None),
        response_format: str = Form("json"),
    ) -> dict[str, str] | PlainTextResponse:
        require(model, settings.stt_profiles)
        if language not in (None, "", "en") and not language.startswith("en"):
            raise SpeechError(422, "UNSUPPORTED_LANGUAGE", "only English is served")
        try:
            samples, rate = decode_wav(await file.read())
        except InvalidAudio as exc:
            raise SpeechError(422, "INVALID_AUDIO", str(exc)) from exc
        text = await run_in_threadpool(speech.transcribe, samples, rate)
        if response_format == "text":
            return PlainTextResponse(text)
        return {"text": text}

    @app.post("/v1/audio/speech", response_model=None)
    async def synthesize(body: SpeechIn) -> StreamingResponse:
        require(body.model, settings.tts_profiles)
        if body.response_format not in MEDIA_TYPES:
            raise SpeechError(
                422, "UNSUPPORTED_FORMAT", f"response_format must be one of {sorted(MEDIA_TYPES)}"
            )
        voice = body.voice or settings.default_voice
        if voice not in speech.voices():
            raise SpeechError(422, "UNKNOWN_VOICE", f"voice must be one of {speech.voices()}")

        async def stream() -> AsyncIterator[bytes]:
            if body.response_format == "wav":
                yield wav_header(OUTPUT_SAMPLE_RATE)
            async for chunk in iterate_in_threadpool(
                speech.synthesize(body.input, voice, body.speed)
            ):
                yield chunk

        return StreamingResponse(stream(), media_type=MEDIA_TYPES[body.response_format])

    return app
