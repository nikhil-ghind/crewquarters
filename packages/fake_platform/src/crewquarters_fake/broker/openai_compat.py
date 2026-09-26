"""OpenAI-compatible model facade (``/internal/v1/sdk/openai/v1``) for LiveKit's OpenAI plugins.

The API key is the run token; ``model`` is a bound profile variant checked against
``llm.profile:<variant>`` like ``/llm/chat``. Unlike ``/llm/chat``, tool calls are allowed here:
voice agents hang up with an ``end_call`` tool. Usage is audited without any content.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from crewquarters_fake.broker.auth import RunAuth, require, run_auth
from crewquarters_fake.broker.llm import prepare_profile
from crewquarters_fake.errors import ApiError
from crewquarters_fake.gateway import ChatReply
from crewquarters_fake.speech import media_type

router = APIRouter(prefix="/openai/v1")
KIND_PREFIX = {"stt": "local.stt.", "tts": "local.tts."}
PIECE_CHARS = 12  # streamed content delta size


def model_kind(model: str) -> str:
    for kind, prefix in KIND_PREFIX.items():
        if model.startswith(prefix):
            return kind
    return "chat"


def require_kind(model: str, kind: str) -> None:
    if model_kind(model) != kind:
        raise ApiError(
            422, "WRONG_MODEL_KIND", f"{model} cannot be used for {kind} (check the profile family)"
        )


def _active_call(auth: RunAuth) -> str:
    """The run's call in progress: fake speech-to-text hears what that call's callee queued."""
    live = [
        c
        for c in auth.store.voice_calls.values()
        if c.run_id == auth.run.id and c.state == "answered"
    ]
    return live[-1].id if live else ""


class SpeechIn(BaseModel):
    model_config = ConfigDict(extra="ignore")  # OpenAI clients also send stream_format, etc.

    model: str
    input: str = Field(min_length=1, max_length=4000)
    voice: str | None = None
    response_format: str = "pcm"
    speed: float = Field(1.0, ge=0.5, le=2.0)


def _pieces(text: str) -> list[str]:
    pieces, current = [], ""
    for word in text.split(" "):
        candidate = f"{current} {word}" if current else word
        if len(candidate) > PIECE_CHARS and current:
            pieces.append(current + " ")
            current = word
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _usage(reply: ChatReply) -> dict[str, int]:
    return {
        "prompt_tokens": reply.input_tokens,
        "completion_tokens": reply.output_tokens,
        "total_tokens": reply.input_tokens + reply.output_tokens,
    }


def _message(reply: ChatReply) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": reply.text if reply.text or not reply.tool_calls else None,
    }
    if reply.tool_calls:
        message["tool_calls"] = reply.tool_calls
    return message


def _sse(reply: ChatReply, model: str, include_usage: bool) -> AsyncIterator[str]:
    completion_id, created = f"chatcmpl-{uuid.uuid4().hex[:24]}", int(time.time())

    def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
        body = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(body)}\n\n"

    async def stream() -> AsyncIterator[str]:
        yield chunk({"role": "assistant", "content": ""})
        for piece in _pieces(reply.text):
            yield chunk({"content": piece})
        for index, call in enumerate(reply.tool_calls):
            yield chunk({"tool_calls": [{"index": index, **call}]})
        yield chunk({}, reply.finish_reason)
        if include_usage:
            usage = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [],
                "usage": _usage(reply),
            }
            yield f"data: {json.dumps(usage)}\n\n"
        yield "data: [DONE]\n\n"

    return stream()


@router.get("/models")
async def list_models(auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        require(auth, None, "broker.openai.models")
        profiles = auth.installation.resolved_profiles
        return {
            "object": "list",
            "data": [{"id": p, "object": "model", "owned_by": "crewquarters"} for p in profiles],
        }

    return await auth.store.faults.run("broker.openai.models", operation)


@router.post("/chat/completions", response_model=None)
async def chat_completions(
    request: Request, auth: RunAuth = Depends(run_auth)
) -> dict[str, Any] | StreamingResponse:
    try:
        body = await request.json()
    except ValueError as exc:
        raise ApiError(422, "INVALID_REQUEST", "the body must be JSON") from exc
    model, messages = body.get("model"), body.get("messages")
    if not isinstance(model, str) or not isinstance(messages, list) or not messages:
        raise ApiError(422, "INVALID_REQUEST", "model and a non-empty messages list are required")

    async def operation() -> tuple[ChatReply, int]:
        require_kind(model, "chat")
        info = await prepare_profile(auth, model, "broker.openai.chat")
        started = time.monotonic()
        reply = await auth.store.gateway.chat_completion(info, body)
        latency = int((time.monotonic() - started) * 1000)
        auth.store.audit_event(
            auth.run,
            "llm.call",
            {
                "profile": model,
                "provider": info.provider,
                "model": info.model,
                "locality": info.locality,
                "inputTokens": reply.input_tokens,
                "outputTokens": reply.output_tokens,
                "toolCalls": len(reply.tool_calls),
                "latencyMs": latency,
                "requestId": request.headers.get("x-request-id") or uuid.uuid4().hex,
            },
        )
        await auth.store.notify()
        return reply, latency

    reply, _ = await auth.store.faults.run("broker.openai.chat", operation)
    if body.get("stream"):
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        return StreamingResponse(_sse(reply, model, include_usage), media_type="text/event-stream")
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": _message(reply), "finish_reason": reply.finish_reason}],
        "usage": _usage(reply),
    }


@router.post("/audio/transcriptions", response_model=None)
async def transcriptions(
    file: UploadFile = File(...),
    model: str = Form(...),
    language: str | None = Form(None),
    response_format: str = Form("json"),
    auth: RunAuth = Depends(run_auth),
) -> dict[str, str] | PlainTextResponse:
    async def operation() -> str:
        require_kind(model, "stt")
        await prepare_profile(auth, model, "broker.openai.transcriptions")
        audio = await file.read()
        started = time.monotonic()
        text = await auth.store.speech.transcribe(audio, model, language, _active_call(auth))
        auth.store.audit_event(
            auth.run,
            "speech.call",
            {
                "profile": model,
                "operation": "transcribe",
                "audioBytes": len(audio),
                "latencyMs": int((time.monotonic() - started) * 1000),
            },
        )
        return text

    text = await auth.store.faults.run("broker.openai.transcriptions", operation)
    if response_format == "text":
        return PlainTextResponse(text)
    return {"text": text}


@router.post("/audio/speech", response_model=None)
async def speech(body: SpeechIn, auth: RunAuth = Depends(run_auth)) -> StreamingResponse:
    async def operation() -> tuple[str, AsyncIterator[bytes], bytes]:
        require_kind(body.model, "tts")
        await prepare_profile(auth, body.model, "broker.openai.speech")
        media = media_type(body.response_format)
        audio = auth.store.speech.synthesize(
            body.model, body.input, body.voice, body.response_format, body.speed
        )
        # Wait for the first chunk so a model error is still an HTTP error, not a cut stream.
        first = await anext(audio, b"")
        auth.store.audit_event(
            auth.run,
            "speech.call",
            {"profile": body.model, "operation": "synthesize", "characters": len(body.input)},
        )
        return media, audio, first

    media, audio, first = await auth.store.faults.run("broker.openai.speech", operation)

    async def stream() -> AsyncIterator[bytes]:
        yield first
        async for chunk in audio:
            yield chunk

    return StreamingResponse(stream(), media_type=media, headers={"x-request-id": uuid.uuid4().hex})
