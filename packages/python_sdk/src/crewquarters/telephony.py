"""Fixed-script outbound calls through the broker (the broker builds the TwiML)."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from urllib.parse import quote

from crewquarters._models import WireModel
from crewquarters._transport import BrokerClient
from crewquarters.errors import InvalidInput
from crewquarters.redact import mask_phone

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
TERMINAL_STATES = frozenset({"completed", "busy", "no-answer", "failed", "canceled"})


def is_e164(value: str) -> bool:
    return bool(E164_RE.match(value))


class Call(WireModel):
    id: str
    idempotency_key: str
    to_masked: str
    state: str
    answered: bool = False
    speech_captured: bool = False
    transcript: str | None = None
    duration_seconds: int | None = None
    error_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES


class TelephonyClient:
    def __init__(self, transport: BrokerClient) -> None:
        self._transport = transport

    async def create_call(
        self, to: str, *, disclosure: str, script: str, gather_seconds: int, idempotency_key: str
    ) -> Call:
        if not is_e164(to):
            raise InvalidInput(f"{mask_phone(to)} is not a valid E.164 number")
        body = {
            "to": to,
            "script": {"disclosure": disclosure, "text": script},
            "gather": {"input": "speech", "timeoutSeconds": gather_seconds},
            "idempotencyKey": idempotency_key,
        }
        data = await self._transport.request(
            "POST", "/telephony/calls", operation="telephony.create", idempotent=True, json=body
        )
        return Call.model_validate(data)

    async def get_call(self, call_id: str) -> Call:
        data = await self._transport.request(
            "GET", f"/telephony/calls/{quote(call_id, safe='')}", operation="telephony.get", idempotent=True
        )
        return Call.model_validate(data)

    async def wait_for_call(self, call_id: str, *, timeout_seconds: float, poll_seconds: float = 2.0) -> Call:
        """Poll until the call reaches a terminal state; returns the last state seen at the timeout."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            call = await self.get_call(call_id)
            if call.terminal or time.monotonic() >= deadline:
                return call
            await asyncio.sleep(poll_seconds)
