"""LiveKit's end-of-turn model running in process (no LiveKit job, no LiveKit secrets)."""

from __future__ import annotations

import pytest
from livekit.agents import llm

from voice_caller.turn_detection import InProcessInference, load_turn_detector


def chat(reply: str) -> llm.ChatContext:
    ctx = llm.ChatContext.empty()
    ctx.add_message(role="assistant", content="Does your cleaning on Tuesday still work?")
    ctx.add_message(role="user", content=reply)
    return ctx


async def test_a_finished_answer_ends_the_turn_and_a_fragment_does_not() -> None:
    detector = await load_turn_detector()
    if detector is None:
        pytest.skip("end-of-turn weights are not cached here (the agent image bakes them in)")
    finished = await detector.predict_end_of_turn(chat("Yes, Tuesday works for me."))
    fragment = await detector.predict_end_of_turn(chat("Well, I think that maybe we could"))
    assert finished > 0.5 > fragment


async def test_missing_weights_fall_back_to_pause_based_endpointing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import livekit.plugins.turn_detector.base as base

    def missing(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("Could not find file")

    monkeypatch.setattr(base, "_download_from_hf_hub", missing)
    assert await load_turn_detector() is None


async def test_the_runner_is_initialized_once() -> None:
    class Runner:
        created = 0

        def __init__(self) -> None:
            Runner.created += 1

        def initialize(self) -> None:
            pass

        def run(self, data: bytes) -> bytes:
            return data[::-1]

    executor = InProcessInference(Runner)
    assert await executor.do_inference("m", b"ab") == b"ba"
    assert await executor.do_inference("m", b"cd") == b"dc"
    assert Runner.created == 1
