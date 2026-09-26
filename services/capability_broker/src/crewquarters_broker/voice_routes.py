"""Routes for realtime voice calls (crewquarters_broker.voice).

* ``/internal/v1/voice/calls``: the control API's owner actions (service token).
* ``/api/v1/callbacks/twilio/voice-status/{id}``: Twilio status callbacks (signed).
* ``/api/v1/callbacks/twilio/media/{id}``: Twilio's Media Stream WebSocket (per-call
  token, plus a signed handshake for live calls). Only the callback tunnel reaches these.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, WebSocket
from pydantic import Field

from crewquarters_broker.callbacks import _twilio_params
from crewquarters_broker.deps import ApiModel, BrokerState, broker_state, internal_auth
from crewquarters_broker.voice import MEDIA_PATH, STATUS_PATH
from crewquarters_shared.errors import invalid

internal = APIRouter(prefix="/internal/v1/voice", dependencies=[Depends(internal_auth)])
public = APIRouter()


class VoiceCallIn(ApiModel):
    user_id: uuid.UUID
    to: str = ""
    confirm: bool = False
    voice: str = Field("female", pattern=r"^[a-z0-9_-]{1,32}$")
    instructions: str = Field("", max_length=1000)
    simulate: bool = False


@internal.post("/calls", summary="Start a realtime voice call (or a simulated session)")
async def start_call(
    body: VoiceCallIn, state: BrokerState = Depends(broker_state)
) -> dict[str, Any]:
    """A live call leaves the device, so the owner must confirm it (``confirm: true``).
    ``simulate`` creates a session without dialing, for tests that play Twilio's part."""
    if not body.simulate and not body.confirm:
        raise invalid("CONFIRMATION_REQUIRED", "Confirm the call first.")
    return await state.voice.create(
        body.user_id, body.to, body.voice, body.instructions, simulate=body.simulate
    )


@internal.get("/calls/{call_id}", summary="A voice call's state and live transcript")
async def get_call(call_id: str, state: BrokerState = Depends(broker_state)) -> dict[str, Any]:
    return state.voice.get(call_id)


@internal.post("/calls/{call_id}/hangup", summary="End a voice call")
async def hangup(call_id: str, state: BrokerState = Depends(broker_state)) -> dict[str, Any]:
    return await state.voice.end(call_id)


@public.post(f"{STATUS_PATH}/{{call_id}}", include_in_schema=False)
async def voice_status(
    call_id: str, request: Request, state: BrokerState = Depends(broker_state)
) -> Response:
    params = await _twilio_params(request, state)
    await state.voice.status_callback(call_id, params)
    return Response(status_code=204)


@public.websocket(f"{MEDIA_PATH}/{{call_id}}")
async def media_stream(ws: WebSocket, call_id: str) -> None:
    state: BrokerState = ws.app.state.broker
    await state.voice.accept_stream(ws, call_id)
