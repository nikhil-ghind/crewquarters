"""A simulated person on the other end of a call: joins the LiveKit room and plays a script.

It speaks its first line when it picks up (people answer with "Hello?") and each later line once
the agent has spoken and gone quiet. With the fake speech engine it first tells the fake
speech-to-text what it is about to say, so the agent "hears" exactly the script.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

import numpy as np

from crewquarters_fake.speech import LocalSpeech
from crewquarters_fake.voice.backend import set_answered, set_ended
from crewquarters_fake.voice.scenario import VOICEMAIL_GREETING, CalleeScenario

if TYPE_CHECKING:
    from crewquarters_fake.store import Store, VoiceCallRecord

log = logging.getLogger(__name__)
SAMPLE_RATE = 24000
FRAME_SAMPLES = SAMPLE_RATE // 50  # 20 ms
SPEECH_RMS = 0.01  # the agent's audio above this level counts as speaking
TTS_PROFILE = "local.tts.small"


class AgentListener:
    """Tracks when the agent is speaking, from the audio the callee receives."""

    def __init__(self) -> None:
        self.last_voice = 0.0
        self.spoke = False
        self._tasks: set[asyncio.Task[None]] = set()

    def attach(self, room: Any) -> None:
        from livekit import rtc

        def on_track(track: Any, _publication: Any, participant: Any) -> None:
            if participant.identity != "callee" and track.kind == rtc.TrackKind.KIND_AUDIO:
                task = asyncio.get_running_loop().create_task(self._consume(track))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

        room.on("track_subscribed", on_track)

    async def _consume(self, track: Any) -> None:
        from livekit import rtc

        stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1)
        async for event in stream:
            samples = np.frombuffer(event.frame.data, dtype=np.int16).astype(np.float32) / 32768.0
            if len(samples) and float(np.sqrt(np.mean(samples * samples))) > SPEECH_RMS:
                self.last_voice = time.monotonic()
                self.spoke = True

    def reset(self) -> None:
        self.spoke = False

    async def wait_turn_end(self, quiet: float, within: float) -> bool:
        """Wait up to ``within`` seconds for the agent to speak and then be quiet for ``quiet``."""
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            if self.spoke and time.monotonic() - self.last_voice >= quiet:
                self.reset()
                return True
            await asyncio.sleep(0.05)
        return False


class SimulatedCallee:
    def __init__(
        self,
        store: Store,
        call: VoiceCallRecord,
        scenario: CalleeScenario,
        *,
        url: str,
        token: str,
    ) -> None:
        self.store, self.call, self.scenario = store, call, scenario
        self.url, self.token = url, token
        self.spoken: list[str] = []

    async def run(self) -> None:
        call, scenario, store = self.call, self.scenario, self.store
        await asyncio.sleep(scenario.ring_seconds)
        if call.state not in {"dialing", "ringing"}:
            return
        if scenario.outcome in {"busy", "no-answer", "failed"}:
            if set_ended(
                call, scenario.outcome, "CALL_FAILED" if scenario.outcome == "failed" else None
            ):
                await store.notify()
            return
        from livekit import rtc

        room = rtc.Room()
        listener = AgentListener()
        listener.attach(room)
        try:
            await room.connect(self.url, self.token)
            source = rtc.AudioSource(SAMPLE_RATE, 1)
            track = rtc.LocalAudioTrack.create_audio_track("callee-mic", source)
            options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            await room.local_participant.publish_track(track, options)
            if not set_answered(call):
                return
            await store.notify()
            await self._play(source, listener)
            await store.wait_until(lambda: call.state != "answered", call.max_duration)
        except Exception:
            log.exception("simulated callee failed on call %s", call.id)
            if set_ended(call, "failed", "CALLEE_ERROR"):
                await store.notify()
        finally:
            with contextlib.suppress(Exception):
                await room.disconnect()

    async def _play(self, source: Any, listener: AgentListener) -> None:
        call, scenario = self.call, self.scenario
        if scenario.outcome == "voicemail":
            await self.say(source, VOICEMAIL_GREETING)
            return
        for index, line in enumerate(scenario.script):
            if call.state != "answered":
                return
            if index > 0 and not await listener.wait_turn_end(quiet=0.8, within=30):
                log.info("call %s: the agent did not take its turn; callee stops", call.id)
                return
            await self.say(source, line)
            listener.reset()
        if scenario.hang_up_after_script:
            await listener.wait_turn_end(quiet=1.2, within=20)
            if set_ended(call, "completed"):
                await self.store.notify()

    async def say(self, source: Any, text: str) -> None:
        from livekit import rtc

        speech = self.store.speech
        self.spoken.append(text)
        log.info("call %s: simulated callee says %r", self.call.id, text)
        audio = b"".join(
            [c async for c in speech.synthesize(TTS_PROFILE, text, self.scenario.voice, "pcm", 1.0)]
        )
        if isinstance(speech, LocalSpeech):
            # The fake speech-to-text "hears" this line once it has finished playing.
            finished = time.monotonic() + len(audio) / (2 * SAMPLE_RATE)
            speech.engine.queue_transcripts([text], channel=self.call.id, available_at=finished)
        for start in range(0, len(audio) - FRAME_SAMPLES * 2 + 1, FRAME_SAMPLES * 2):
            frame = audio[start : start + FRAME_SAMPLES * 2]
            await source.capture_frame(rtc.AudioFrame(frame, SAMPLE_RATE, 1, FRAME_SAMPLES))
        silence = bytes(FRAME_SAMPLES * 2)
        for _ in range(15):  # 300 ms of silence so voice-activity detection closes the turn
            await source.capture_frame(rtc.AudioFrame(silence, SAMPLE_RATE, 1, FRAME_SAMPLES))
