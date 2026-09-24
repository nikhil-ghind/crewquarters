"""Broker telephony routes. The broker, not the agent, builds the call script (TwiML in production)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from crewquarters_fake.broker.audit import audited, require_connection
from crewquarters_fake.broker.auth import RunAuth, require, run_auth
from crewquarters_fake.errors import ApiError

router = APIRouter()


class ScriptIn(BaseModel):
    disclosure: str = Field(min_length=1)
    text: str = Field(min_length=1)


class GatherIn(BaseModel):
    input: Literal["speech"]
    timeoutSeconds: int = Field(ge=1, le=60)


class CallIn(BaseModel):
    to: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")
    script: ScriptIn
    gather: GatherIn
    idempotencyKey: str = Field(min_length=1, max_length=200)


@router.post("/telephony/calls")
async def create_call(body: CallIn, request: Request, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    require(auth, "twilio.voice.call", "broker.telephony.create")
    require_connection(auth, "twilio")

    async def call() -> dict[str, Any]:
        return auth.store.twilio.create(
            auth.run.id, body.to, body.script.model_dump(), body.gather.model_dump(), body.idempotencyKey
        )

    return await audited(auth, request, "twilio", "broker.telephony.create", call)


@router.get("/telephony/calls/{call_id}")
async def get_call(call_id: str, request: Request, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    require(auth, "twilio.voice.call", "broker.telephony.get")
    require_connection(auth, "twilio")

    async def call() -> dict[str, Any]:
        found = auth.store.twilio.find(call_id)
        if found is None or found.run_id != auth.run.id:
            raise ApiError(404, "NOT_FOUND", f"call {call_id} not found")
        return auth.store.twilio.advance(found)

    return await audited(auth, request, "twilio", "broker.telephony.get", call)
