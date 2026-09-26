"""Live phone conversations (``ctx.voice``): the broker dials, the agent joins the call's room.

The broker holds the telephony and media-server credentials. ``dial`` returns a ``VoiceCall`` whose
``room`` carries a LiveKit URL and a token for that one room, which the agent uses to hold the
conversation (for example with LiveKit Agents). Numbers are E.164; only the last four digits ever
come back.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import quote

from crewquarters._models import WireModel
from crewquarters._transport import BrokerClient
from crewquarters.errors import InvalidInput
from crewquarters.redact import mask_phone
from crewquarters.telephony import is_e164

VoiceState = Literal[
    "dialing", "ringing", "answered", "completed", "busy", "no-answer", "failed", "canceled"
]
ENDED_STATES = frozenset({"completed", "busy", "no-answer", "failed", "canceled"})


class VoiceRoom(WireModel):
    url: str
    name: str
    token: str
    identity: str


class VoiceCall(WireModel):
    id: str
    idempotency_key: str
    to_masked: str
    state: VoiceState
    room: VoiceRoom | None = None
    callee_identity: str | None = None
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    error_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def ended(self) -> bool:
        return self.state in ENDED_STATES


class VoiceClient:
    def __init__(self, transport: BrokerClient) -> None:
        self._transport = transport

    async def dial(
        self,
        to: str,
        *,
        idempotency_key: str,
        ring_timeout_seconds: int = 30,
        max_duration_seconds: int = 300,
    ) -> VoiceCall:
        """Dial ``to``. The same ``idempotency_key`` always returns the same call, so this is
        retried safely; wrap it in ``ctx.idempotency.once`` to survive a retried run too."""
        if not is_e164(to):
            raise InvalidInput(f"{mask_phone(to)} is not a valid E.164 number")
        data = await self._transport.request(
            "POST",
            "/voice/calls",
            operation="voice.dial",
            idempotent=True,
            json={
                "to": to,
                "idempotencyKey": idempotency_key,
                "ringTimeoutSeconds": ring_timeout_seconds,
                "maxDurationSeconds": max_duration_seconds,
            },
        )
        return VoiceCall.model_validate(data)

    async def get(self, call_id: str) -> VoiceCall:
        data = await self._transport.request(
            "GET",
            f"/voice/calls/{quote(call_id, safe='')}",
            operation="voice.get",
            idempotent=True,
        )
        return VoiceCall.model_validate(data)

    async def hangup(self, call_id: str) -> VoiceCall:
        data = await self._transport.request(
            "POST",
            f"/voice/calls/{quote(call_id, safe='')}/hangup",
            operation="voice.hangup",
            idempotent=True,
        )
        return VoiceCall.model_validate(data)
