"""The simulated callee has its lines ready before it answers, like a person would, so its replies
come promptly however slow speech synthesis is (no LiveKit server needed)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from crewquarters_fake.settings import FakeSettings
from crewquarters_fake.store import Store
from crewquarters_fake.voice.callee import SimulatedCallee
from crewquarters_fake.voice.scenario import VOICEMAIL_GREETING, CalleeScenario


class SlowSpeech:
    """Takes a noticeable time per line, like real synthesis on a busy CPU."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.synthesized: list[str] = []

    async def synthesize(self, *args: Any) -> AsyncIterator[bytes]:
        text = args[1]
        await asyncio.sleep(self.delay)
        self.synthesized.append(text)
        yield bytes(960)  # 20 ms of 24 kHz s16le audio


class Mic:
    def __init__(self) -> None:
        self.frames = 0

    async def capture_frame(self, _frame: Any) -> None:
        self.frames += 1


def callee(scenario: CalleeScenario, speech: SlowSpeech) -> SimulatedCallee:
    store = Store(FakeSettings())
    store.speech = speech  # type: ignore[assignment]
    call = store.new_voice_call("run", "k", "+15555550101", ring_timeout=5, max_duration=60)
    return SimulatedCallee(store, call, scenario, url="ws://unused", token="unused")


async def test_the_script_is_synthesized_before_answering() -> None:
    speech = SlowSpeech(delay=0.2)
    person = callee(CalleeScenario(script=("Hello?", "Yes, I have a minute.")), speech)
    await person.prepare()
    assert speech.synthesized == ["Hello?", "Yes, I have a minute."]

    mic = Mic()
    started = asyncio.get_running_loop().time()
    await person.say(mic, "Yes, I have a minute.")
    assert asyncio.get_running_loop().time() - started < 0.1  # no synthesis while it is its turn
    assert speech.synthesized == ["Hello?", "Yes, I have a minute."]
    assert mic.frames > 0


async def test_a_voicemail_prepares_its_greeting() -> None:
    speech = SlowSpeech(delay=0)
    box = callee(CalleeScenario(outcome="voicemail"), speech)
    await box.prepare()
    assert speech.synthesized == [VOICEMAIL_GREETING]
