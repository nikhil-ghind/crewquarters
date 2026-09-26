"""The voice backend interface and the call state transitions shared by every backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from crewquarters_fake.timeutil import utcnow

if TYPE_CHECKING:
    from crewquarters_fake.store import Store, VoiceCallRecord

ACTIVE = frozenset({"dialing", "ringing", "answered"})
ENDED = frozenset({"completed", "busy", "no-answer", "failed", "canceled"})


@dataclass(frozen=True)
class VoiceRoom:
    url: str
    name: str
    token: str
    identity: str


class VoiceBackend(Protocol):
    async def dial(self, store: Store, call: VoiceCallRecord) -> None:
        """Start dialing; state changes happen in background tasks."""
        ...

    async def hangup(self, store: Store, call: VoiceCallRecord) -> None: ...

    def room(self, call: VoiceCallRecord) -> VoiceRoom: ...


def set_ringing(call: VoiceCallRecord) -> None:
    if call.state == "dialing":
        call.state = "ringing"
        call.updated_at = utcnow()


def set_answered(call: VoiceCallRecord) -> bool:
    if call.state not in {"dialing", "ringing"}:
        return False
    call.state = "answered"
    call.answered_at = call.updated_at = utcnow()
    return True


def set_ended(call: VoiceCallRecord, state: str, error_code: str | None = None) -> bool:
    """End the call once; later calls are no-ops so the first reason sticks."""
    if call.state in ENDED:
        return False
    call.state = state
    call.ended_at = call.updated_at = utcnow()
    if error_code is not None:
        call.error_code = error_code
    return True


def end_for_hangup(call: VoiceCallRecord) -> bool:
    return set_ended(call, "completed" if call.state == "answered" else "canceled")
