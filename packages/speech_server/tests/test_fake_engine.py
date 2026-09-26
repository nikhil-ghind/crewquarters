"""The fake engine's transcript matching, which simulated callees rely on."""

from __future__ import annotations

import numpy as np

from crewquarters_speech.fake import FakeSpeechEngine

SILENCE = np.zeros(1600, dtype=np.float32)


def hear(engine: FakeSpeechEngine, channel: str = "call") -> str:
    return engine.transcribe(SILENCE, 16000, channel)


def test_untimed_lines_are_heard_in_order() -> None:
    engine = FakeSpeechEngine()
    engine.queue_transcripts(["one", "two"], "call")
    assert [hear(engine), hear(engine), hear(engine)] == ["one", "two", ""]


def test_a_line_is_not_heard_before_it_has_been_spoken() -> None:
    clock = [100.0]
    engine = FakeSpeechEngine(clock=lambda: clock[0])
    engine.queue_transcripts(["Tuesday works."], "call", available_at=103.0)
    assert hear(engine) == ""  # the voice-activity detector cut a segment mid-line
    clock[0] = 103.1
    assert hear(engine) == "Tuesday works."
    assert hear(engine) == ""  # a second segment of the same line hears nothing new


def test_a_line_that_was_never_segmented_is_skipped_not_replayed_late() -> None:
    clock = [100.0]
    engine = FakeSpeechEngine(clock=lambda: clock[0])
    engine.queue_transcripts(["Yes, I have a minute."], "call", available_at=101.0)
    engine.queue_transcripts(["Tuesday works."], "call", available_at=104.0)
    clock[0] = 104.2
    assert hear(engine) == "Tuesday works."
    assert hear(engine) == ""


def test_channels_are_separate() -> None:
    engine = FakeSpeechEngine()
    engine.queue_transcripts(["first call"], "a")
    assert hear(engine, "b") == ""
    assert hear(engine, "a") == "first call"
