from __future__ import annotations

from voice_helpers import make_config

from voice_caller.outcome import (
    CallOutcome,
    classification_messages,
    outcome_for_machine,
    outcome_for_unanswered,
    reconcile,
)
from voice_caller.transcript import Transcript


def transcript(*callee_lines: str) -> Transcript:
    t = Transcript()
    t.add("agent", "Hi Asha, this is Sam, an automated AI assistant calling for Acme Dental.")
    for line in callee_lines:
        t.add("callee", line)
    return t


def test_do_not_call_maps_to_dnc_whatever_the_model_says() -> None:
    model = CallOutcome(disposition="declined", interest="none")
    for line in [
        "Please don't call me again.",
        "Stop calling this number",
        "Remove me from your list",
    ]:
        assert reconcile(model, transcript(line)).disposition == "dnc"


def test_an_answered_call_is_never_recorded_as_unanswered() -> None:
    model = CallOutcome(disposition="no_answer")
    assert reconcile(model, transcript("Yes, Tuesday works.")).disposition == "completed"


def test_notes_never_carry_phone_numbers() -> None:
    model = CallOutcome(disposition="callback", notes="Call back on +14155550123 after five.")
    notes = reconcile(model, transcript("Call me back later")).notes
    assert "+14155550123" not in notes and "0123" in notes


def test_unanswered_and_machine_outcomes() -> None:
    assert outcome_for_unanswered("busy").disposition == "busy"
    assert outcome_for_unanswered("no-answer").disposition == "no_answer"
    assert outcome_for_unanswered("canceled").disposition == "failed"
    assert outcome_for_machine("voicemail_reached").disposition == "voicemail"


def test_classification_prompt_treats_the_transcript_as_data() -> None:
    messages = classification_messages(make_config(), transcript("My number is +14155550123."))
    assert "untrusted data" in messages[0]["content"]
    assert "+14155550123" not in messages[1]["content"]
    assert "Callee: My number is" in messages[1]["content"]
