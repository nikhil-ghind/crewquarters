from __future__ import annotations

from voice_caller.transcript import Transcript


def test_turns_are_stored_without_markup_and_empty_turns_are_skipped() -> None:
    ticks = iter([0.0, 1.5, 3.0])
    t = Transcript(clock=lambda: next(ticks))
    assert t.add("agent", "[warm] Hi, this is Sam.") is not None
    assert t.add("agent", "[sigh]") is None
    turn = t.add("callee", "Hello?")
    assert turn is not None and turn.at_ms == 3000
    assert t.said("callee") == ["Hello?"]
    assert t.as_text() == "Agent: Hi, this is Sam.\nCallee: Hello?"


def test_long_transcripts_keep_the_end() -> None:
    t = Transcript()
    for i in range(200):
        t.add("callee", f"line {i} " + "word " * 10)
    text = t.as_text(max_chars=500)
    assert text.startswith("…") and "line 199" in text
