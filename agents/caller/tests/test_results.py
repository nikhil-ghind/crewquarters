from typing import Any

import pytest

from caller_agent.results import HEADER, display_status, row_values
from caller_agent.rows import ContactRow
from crewquarters.telephony import Call


def call(state: str, **extra: Any) -> Call:
    return Call.model_validate(
        {"id": "CA1", "idempotencyKey": "k", "toMasked": "••••0101", "state": state, **extra}
    )


@pytest.mark.parametrize(
    ("state", "extra", "expected"),
    [
        ("completed", {"answered": True, "speechCaptured": True, "transcript": "yes"}, "answered_speech"),
        ("completed", {"answered": True}, "answered_no_speech"),
        ("busy", {}, "busy"),
        ("no-answer", {}, "no_answer"),
        ("failed", {"errorCode": "unverified-number"}, "failed"),
        ("canceled", {}, "canceled"),
        ("ringing", {}, "timeout"),
        ("in-progress", {"answered": True}, "timeout"),
    ],
)
def test_display_status(state: str, extra: dict[str, Any], expected: str) -> None:
    assert display_status(call(state, **extra)) == expected


def test_missing_call_is_failed() -> None:
    assert display_status(None) == "failed"


def test_row_values_mask_the_number_and_fill_eight_columns() -> None:
    contact = ContactRow(4, "Asha", "+15555550101", "yes", "")
    values = row_values(
        contact, call("completed", transcript="Yes"), "answered_speech", "2026-09-24T10:00:00+05:30", None
    )
    assert values == [
        "4",
        "Asha",
        "••••0101",
        "CA1",
        "answered_speech",
        "Yes",
        "2026-09-24T10:00:00+05:30",
        "",
    ]
    assert len(HEADER) == len(values) == 8
    failed = row_values(contact, None, "failed", None, "provider error")
    assert failed == ["4", "Asha", "••••0101", "", "failed", "", "", "provider error"]
