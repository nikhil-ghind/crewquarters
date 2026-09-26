"""Broker voice-call routes (``ctx.voice``): dial through the voice backend, poll, hang up."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from crewquarters_fake.broker.auth import RunAuth, require, run_auth
from crewquarters_fake.errors import ApiError
from crewquarters_fake.store import VoiceCallRecord
from crewquarters_fake.views import voice_call_view

router = APIRouter()
CAPABILITY = "sip.call.conversational"


class VoiceCallIn(BaseModel):
    to: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")
    idempotencyKey: str = Field(min_length=1, max_length=200)
    ringTimeoutSeconds: int = Field(30, ge=5, le=120)
    maxDurationSeconds: int = Field(300, ge=30, le=1800)


def _view(auth: RunAuth, call: VoiceCallRecord) -> dict[str, Any]:
    return voice_call_view(call, auth.store.voice_backend.room(call))


def _own_call(auth: RunAuth, call_id: str) -> VoiceCallRecord:
    call = auth.store.voice_calls.get(call_id)
    if call is None or call.run_id != auth.run.id:
        raise ApiError(404, "NOT_FOUND", f"call {call_id} not found")
    return call


@router.post("/voice/calls")
async def create_call(body: VoiceCallIn, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        require(auth, CAPABILITY, "broker.voice.create")
        store = auth.store
        existing = store.voice_keys.get((auth.run.id, body.idempotencyKey))
        if existing is not None:
            return _view(auth, store.voice_calls[existing])
        call = store.new_voice_call(
            auth.run.id,
            body.idempotencyKey,
            body.to,
            ring_timeout=body.ringTimeoutSeconds,
            max_duration=body.maxDurationSeconds,
        )
        try:
            await store.voice_backend.dial(store, call)
        except Exception as exc:
            call.state, call.error_code = "failed", "DIAL_FAILED"
            store.audit_event(auth.run, "voice.dial", {"callId": call.id, "outcome": "error"})
            raise ApiError(502, "PROVIDER_ERROR", f"the call could not be placed: {exc}") from exc
        store.audit_event(auth.run, "voice.dial", {"callId": call.id, "outcome": "ok"})
        await store.notify()
        return _view(auth, call)

    return await auth.store.faults.run("broker.voice.create", operation)


@router.get("/voice/calls/{call_id}")
async def get_call(call_id: str, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        require(auth, CAPABILITY, "broker.voice.get")
        return _view(auth, _own_call(auth, call_id))

    return await auth.store.faults.run("broker.voice.get", operation)


@router.post("/voice/calls/{call_id}/hangup")
async def hangup_call(call_id: str, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        # Hanging up must work even while a cancel is pending, so no capability check here
        # beyond owning the call: ending a call can only reduce what the agent is doing.
        require(auth, None, "broker.voice.hangup")
        call = _own_call(auth, call_id)
        await auth.store.voice_backend.hangup(auth.store, call)
        auth.store.audit_event(auth.run, "voice.hangup", {"callId": call.id, "state": call.state})
        return _view(auth, call)

    return await auth.store.faults.run("broker.voice.hangup", operation)
