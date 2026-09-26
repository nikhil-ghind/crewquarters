from __future__ import annotations

from voice_helpers import make_config

from caller_agent.rows import ContactRow
from voice_caller.outcome import CallOutcome
from voice_caller.results import HEADER, result_range, row_values

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
