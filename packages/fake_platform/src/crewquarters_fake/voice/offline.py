"""A voice backend with no media: the callee scenario drives the call state on a clock."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from crewquarters_fake.voice.backend import (
    VoiceRoom,
    end_for_hangup,
    set_answered,
    set_ended,
    set_ringing,
)
from crewquarters_fake.voice.scenario import UNKNOWN_NUMBER

if TYPE_CHECKING:
    from crewquarters_fake.store import Store, VoiceCallRecord


# Offline calls have no media server; the "token" only fills the VoiceRoom shape.
OFFLINE_TOKEN = "offline"  # noqa: S105 - a placeholder, not a credential


class OfflineVoiceBackend:
    async def dial(self, store: Store, call: VoiceCallRecord) -> None:
        set_ringing(call)
        store.spawn(self._progress(store, call))

    async def _progress(self, store: Store, call: VoiceCallRecord) -> None:
        scenario = store.voice_callees.get(call.to, UNKNOWN_NUMBER)
        ring = scenario.ring_seconds
        await asyncio.sleep(min(ring, call.ring_timeout))
        if scenario.outcome in {"answer", "voicemail"} and ring < call.ring_timeout:
            if set_answered(call):
                await store.notify()
                await store.wait_until(lambda: call.state != "answered", call.max_duration)
                if set_ended(call, "completed", "MAX_DURATION"):
                    await store.notify()
            return
        outcome = (
            scenario.outcome if scenario.outcome not in {"answer", "voicemail"} else "no-answer"
        )
        if set_ended(call, outcome, "CALL_FAILED" if outcome == "failed" else None):
            await store.notify()

    async def hangup(self, store: Store, call: VoiceCallRecord) -> None:
        if end_for_hangup(call):
            await store.notify()

    def room(self, call: VoiceCallRecord) -> VoiceRoom:
        return VoiceRoom(
            url="offline://voice", name=call.room_name, token=OFFLINE_TOKEN, identity="agent"
        )
