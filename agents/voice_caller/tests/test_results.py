from __future__ import annotations

from voice_helpers import make_config

from caller_agent.rows import ContactRow
from voice_caller.outcome import CallOutcome
from voice_caller.results import HEADER, STATUS_AFTER, result_range, row_values, status_range

CONTACT = ContactRow(4, "Asha Rao", "+14155550123", "yes", "")


def test_result_row_masks_the_number() -> None:
    outcome = CallOutcome(disposition="completed", interest="high", notes="Keeps Tuesday.")
    values = row_values(CONTACT, "vc-1", outcome, 42, "2026-09-25T10:00:00Z")
    assert len(values) == len(HEADER) == 10
    assert values[:5] == ["4", "Asha Rao", "••••0123", "vc-1", "completed"]
    assert "+14155550123" not in values


def test_ranges_follow_the_contact_row() -> None:
    config = make_config()
    assert result_range(config, 4) == "Results!A4:J4"
    assert status_range(config, 4) == "Contacts!D4"


def test_only_answered_dispositions_change_the_contact_status() -> None:
    assert STATUS_AFTER["dnc"] == "dnc"
    assert STATUS_AFTER["completed"] == "called"
    for unanswered in ("voicemail", "no_answer", "busy", "failed"):
        assert unanswered not in STATUS_AFTER
