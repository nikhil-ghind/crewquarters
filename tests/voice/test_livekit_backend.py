"""The fake broker's LiveKit backend against a real local LiveKit server (marker `voice`)."""

from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest
from livekit import rtc

from crewquarters_fake.settings import FakeSettings
from crewquarters_fake.store import Store, VoiceCallRecord
from crewquarters_fake.voice.scenario import parse_callees

pytestmark = pytest.mark.voice
NUMBER = "+15555550101"


def store_for(livekit_url: str, spec: dict[str, object]) -> Store:
    store = Store(FakeSettings(livekit_url=livekit_url))
    store.voice_callees = parse_callees(spec)
    return store


async def wait(predicate, seconds: float) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return bool(predicate())


async def join_as_agent(store: Store, call: VoiceCallRecord) -> tuple[rtc.Room, list[float]]:
    """Join with the agent's room token and record the loudness of what the callee says."""
    room, levels = rtc.Room(), []

    def on_track(
        track: rtc.Track, _pub: rtc.RemoteTrackPublication, who: rtc.RemoteParticipant
    ) -> None:
        if who.identity == "callee" and track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.get_running_loop().create_task(listen(track))

    async def listen(track: rtc.Track) -> None:
        async for event in rtc.AudioStream(track, sample_rate=24000, num_channels=1):
            samples = np.frombuffer(event.frame.data, dtype=np.int16).astype(np.float32) / 32768
            levels.append(float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0)

    room.on("track_subscribed", on_track)
    grant = store.voice_backend.room(call)
    await room.connect(grant.url, grant.token)
    return room, levels


async def test_simulated_callee_answers_and_speaks(livekit_url: str) -> None:
    store = store_for(
        livekit_url, {NUMBER: {"outcome": "answer", "ringSeconds": 0.3, "script": ["Hello?"]}}
    )
    call = store.new_voice_call("run-1", "k1", NUMBER, ring_timeout=10, max_duration=60)
    await store.voice_backend.dial(store, call)
    room, levels = await join_as_agent(store, call)
    try:
        assert await wait(lambda: call.state == "answered", 10)
        assert await wait(lambda: any(level > 0.02 for level in levels), 10), "no callee audio"
        # The fake speech-to-text was told what the callee said before it said it.
        assert store.fake_speech._transcripts[call.id][0][0] == "Hello?"
        await store.voice_backend.hangup(store, call)
        assert call.state == "completed" and call.duration_seconds is not None
    finally:
        await room.disconnect()
        await store.voice_backend.aclose()  # type: ignore[attr-defined]


async def test_busy_callee_never_joins(livekit_url: str) -> None:
    store = store_for(livekit_url, {NUMBER: {"outcome": "busy", "ringSeconds": 0.2}})
    call = store.new_voice_call("run-1", "k2", NUMBER, ring_timeout=10, max_duration=60)
    await store.voice_backend.dial(store, call)
    assert await wait(lambda: call.state == "busy", 5)
    await store.voice_backend.aclose()  # type: ignore[attr-defined]


async def test_unanswered_call_times_out(livekit_url: str) -> None:
    store = store_for(livekit_url, {NUMBER: {"outcome": "answer", "ringSeconds": 5}})
    call = store.new_voice_call("run-1", "k3", NUMBER, ring_timeout=1, max_duration=60)
    await store.voice_backend.dial(store, call)
    assert await wait(lambda: call.state == "no-answer", 5)
    await store.voice_backend.aclose()  # type: ignore[attr-defined]
