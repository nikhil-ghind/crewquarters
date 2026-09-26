"""Voice calls through a real (local) LiveKit server.

The broker holds the LiveKit API key and secret. For each call it creates a room and gives the
agent a token for that room only. With a SIP trunk configured it dials the number through
LiveKit SIP; otherwise a simulated callee joins the room and plays the scenario.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from crewquarters_fake.timeutil import utcnow
from crewquarters_fake.voice.backend import (
    VoiceRoom,
    end_for_hangup,
    set_answered,
    set_ended,
    set_ringing,
)
from crewquarters_fake.voice.callee import SimulatedCallee
from crewquarters_fake.voice.scenario import UNKNOWN_NUMBER

if TYPE_CHECKING:
    from crewquarters_fake.settings import FakeSettings
    from crewquarters_fake.store import Store, VoiceCallRecord

log = logging.getLogger(__name__)
CALLEE = "callee"
AGENT = "agent"
# SIP response codes LiveKit SIP reports when a call is not answered.
_SIP_OUTCOME = {"486": "busy", "600": "busy", "480": "no-answer", "408": "no-answer"}


def http_url(ws_url: str) -> str:
    return ws_url.replace("wss://", "https://").replace("ws://", "http://")


class LiveKitVoiceBackend:
    def __init__(self, settings: FakeSettings) -> None:
        if not settings.livekit_url:
            raise ValueError("LiveKitVoiceBackend needs CREWQ_FAKE_LIVEKIT_URL")
        self.settings = settings
        self.agent_url: str = settings.livekit_url
        self.callee_url: str = settings.livekit_callee_url or settings.livekit_url
        self.api_url: str = settings.livekit_api_url or http_url(self.callee_url)
        self._api: Any = None

    async def _lk(self) -> Any:
        from livekit import api

        if self._api is None:
            self._api = api.LiveKitAPI(
                self.api_url, self.settings.livekit_api_key, self.settings.livekit_api_secret
            )
        return self._api

    def token(self, call: VoiceCallRecord, identity: str) -> str:
        from livekit import api

        ttl = timedelta(seconds=call.ring_timeout + call.max_duration + 60)
        grants = api.VideoGrants(
            room_join=True,
            room=call.room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
        )
        return (
            api.AccessToken(self.settings.livekit_api_key, self.settings.livekit_api_secret)
            .with_identity(identity)
            .with_name(identity)
            .with_grants(grants)
            .with_ttl(ttl)
            .to_jwt()
        )

    def room(self, call: VoiceCallRecord) -> VoiceRoom:
        return VoiceRoom(
            url=self.agent_url, name=call.room_name, token=self.token(call, AGENT), identity=AGENT
        )

    async def dial(self, store: Store, call: VoiceCallRecord) -> None:
        from livekit import api

        lk = await self._lk()
        await lk.room.create_room(
            api.CreateRoomRequest(name=call.room_name, empty_timeout=60, max_participants=4)
        )
        set_ringing(call)
        if self.settings.sip_trunk_id:
            store.spawn(self._dial_sip(store, call))
        else:
            scenario = store.voice_callees.get(call.to, UNKNOWN_NUMBER)
            callee = SimulatedCallee(
                store, call, scenario, url=self.callee_url, token=self.token(call, CALLEE)
            )
            store.spawn(callee.run())
        store.spawn(self._enforce_limits(store, call))

    async def _dial_sip(self, store: Store, call: VoiceCallRecord) -> None:
        from google.protobuf.duration_pb2 import Duration
        from livekit import api

        lk = await self._lk()
        request = api.CreateSIPParticipantRequest(
            sip_trunk_id=self.settings.sip_trunk_id,
            sip_call_to=call.to,
            room_name=call.room_name,
            participant_identity=CALLEE,
            participant_name="Callee",
            wait_until_answered=True,
            ringing_timeout=Duration(seconds=int(call.ring_timeout)),
            max_call_duration=Duration(seconds=int(call.max_duration)),
        )
        try:
            await lk.sip.create_sip_participant(request)
        except api.TwirpError as exc:
            code = (exc.metadata or {}).get("sip_status_code", "")
            outcome = _SIP_OUTCOME.get(code, "failed")
            log.info("SIP call %s not answered: %s %s", call.id, code or exc.code, exc.message)
            if set_ended(call, outcome, None if outcome != "failed" else f"SIP_{code or 'ERROR'}"):
                await self._delete_room(call)
                await store.notify()
            return
        if set_answered(call):
            await store.notify()

    async def _enforce_limits(self, store: Store, call: VoiceCallRecord) -> None:
        answered = await store.wait_until(lambda: call.state != "ringing", call.ring_timeout)
        if not answered and set_ended(call, "no-answer"):
            await self._delete_room(call)
            await store.notify()
            return
        if call.state != "answered":
            return
        while call.state == "answered":
            ended = await store.wait_until(lambda: call.state != "answered", 1.0)
            if ended:
                break
            if call.answered_at is not None and self._elapsed(call) >= call.max_duration:
                if set_ended(call, "completed", "MAX_DURATION"):
                    await self._delete_room(call)
                    await store.notify()
                break
            if self.settings.sip_trunk_id and not await self._callee_present(call):
                if set_ended(call, "completed"):  # the person hung up
                    await self._delete_room(call)
                    await store.notify()
                break

    @staticmethod
    def _elapsed(call: VoiceCallRecord) -> float:
        if call.answered_at is None:
            return 0.0
        return (utcnow() - call.answered_at).total_seconds()

    async def _callee_present(self, call: VoiceCallRecord) -> bool:
        from livekit import api

        try:
            lk = await self._lk()
            people = await lk.room.list_participants(
                api.ListParticipantsRequest(room=call.room_name)
            )
        except Exception:  # the room is gone
            return False
        return any(p.identity == CALLEE for p in people.participants)

    async def hangup(self, store: Store, call: VoiceCallRecord) -> None:
        if end_for_hangup(call):
            await self._delete_room(call)
            await store.notify()

    async def _delete_room(self, call: VoiceCallRecord) -> None:
        from livekit import api

        try:
            lk = await self._lk()
            await lk.room.delete_room(api.DeleteRoomRequest(room=call.room_name))
        except Exception as exc:  # already gone, or LiveKit is down: nothing left to hang up
            log.info("room %s not deleted: %s", call.room_name, exc)

    async def aclose(self) -> None:
        if self._api is not None:
            await self._api.aclose()
            self._api = None
