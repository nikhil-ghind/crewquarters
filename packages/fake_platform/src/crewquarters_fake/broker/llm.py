"""Broker LLM routes (model-gateway stand-in): profile grants, cold start, caching, streaming."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from crewquarters_fake.broker.audit import request_id
from crewquarters_fake.broker.auth import RunAuth, deny, require, run_auth
from crewquarters_fake.errors import ApiError
from crewquarters_fake.gateway import ProfileInfo

router = APIRouter()


class MessageIn(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str


class ChatIn(BaseModel):
    profile: str
    messages: list[MessageIn] = Field(min_length=1)
    temperature: float | None = Field(None, ge=0, le=2)
    maxOutputTokens: int | None = Field(None, ge=1)
    responseSchema: dict[str, Any] | None = None
    tools: list[Any] = []
    idempotencyKey: str | None = None


CLOUD_PROVIDERS = frozenset({"openai", "anthropic"})


async def _prepare(auth: RunAuth, body: ChatIn, operation: str) -> ProfileInfo:
    """Capabilities per packages/contracts/capabilities.yaml: ``llm.profile:<variant>`` for every
    profile, plus ``cloud.<provider>`` for cloud profiles."""
    require(auth, f"llm.profile:{body.profile}", operation)
    provider = body.profile.split(".", 1)[0]
    if provider in CLOUD_PROVIDERS:
        require(auth, f"cloud.{provider}", operation)
    if body.tools:
        raise ApiError(422, "UNSUPPORTED_FEATURE", "tools are not supported in v1alpha1")
    if body.profile not in auth.installation.resolved_profiles:
        deny(auth, f"llm.profile:{body.profile}", operation)
    info = auth.store.gateway.profile(body.profile)
    gateway, store, run = auth.store.gateway, auth.store, auth.run
    cold = body.profile not in gateway.loaded and gateway.cold_start_seconds > 0
    if info.locality == "local" and cold:
        if run.state == "RUNNING":
            store.transition(run, "LOADING_MODEL", f"loading {body.profile}")
            await store.notify()
        await asyncio.sleep(gateway.cold_start_seconds)
        if run.state == "LOADING_MODEL":
            store.transition(run, "RUNNING", f"{body.profile} ready")
            await store.notify()
    gateway.loaded.add(body.profile)
    return info


async def _complete(auth: RunAuth, body: ChatIn, info: ProfileInfo, req_id: str) -> dict[str, Any]:
    started = time.monotonic()
    request = body.model_dump()
    completion = await auth.store.gateway.complete(info, request)
    latency = int((time.monotonic() - started) * 1000)
    response = {
        "text": completion.text,
        "structured": completion.structured,
        "finishReason": completion.finish_reason,
        "usage": {"inputTokens": completion.input_tokens, "outputTokens": completion.output_tokens},
        "provider": info.provider,
        "model": info.model,
        "locality": info.locality,
        "latencyMs": latency,
        "requestId": req_id,
    }
    auth.store.gateway.log.append(
        {
            "runId": auth.run.id,
            "profile": body.profile,
            "messages": request["messages"],
            "responseSchema": body.responseSchema,
            "response": completion.text,
        }
    )
    auth.store.audit_event(
        auth.run,
        "llm.call",
        {
            "profile": body.profile,
            "provider": info.provider,
            "model": info.model,
            "locality": info.locality,
            "inputTokens": completion.input_tokens,
            "outputTokens": completion.output_tokens,
            "latencyMs": latency,
            "requestId": req_id,
        },
    )
    await auth.store.notify()
    return response


@router.post("/llm/chat")
async def chat(body: ChatIn, request: Request, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        cache_key = (auth.run.id, body.idempotencyKey) if body.idempotencyKey else None
        if cache_key and cache_key in auth.store.gateway.cache:
            return auth.store.gateway.cache[cache_key]
        info = await _prepare(auth, body, "broker.llm.chat")
        response = await _complete(auth, body, info, request_id(request))
        if cache_key:
            auth.store.gateway.cache[cache_key] = response
        return response

    return await auth.store.faults.run("broker.llm.chat", operation)


@router.post("/llm/chat:stream")
async def chat_stream(
    body: ChatIn, request: Request, auth: RunAuth = Depends(run_auth)
) -> StreamingResponse:
    async def prepare() -> ProfileInfo:
        return await _prepare(auth, body, "broker.llm.stream")

    info = await auth.store.faults.run("broker.llm.stream", prepare)
    req_id = request_id(request)

    async def stream() -> AsyncIterator[str]:
        try:
            response = await _complete(auth, body, info, req_id)
        except ApiError as exc:
            error = {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "requestId": req_id,
                    "details": exc.details,
                }
            }
            yield f"event: error\ndata: {json.dumps(error)}\n\n"
            return
        text = response["text"]
        for start in range(0, len(text), 16):
            yield f"event: delta\ndata: {json.dumps({'text': text[start : start + 16]})}\n\n"
        yield f"event: done\ndata: {json.dumps(response)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
