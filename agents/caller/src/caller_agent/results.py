"""Call status mapping and result-sheet rows (columns A-H)."""

from __future__ import annotations

from caller_agent.rows import ContactRow
from crewquarters.redact import mask_phone
from crewquarters.telephony import Call

HEADER = ["source_row", "name", "phone_masked", "call_sid", "status", "transcript", "completed_at", "error"]
_TERMINAL_STATUS = {"busy": "busy", "no-answer": "no_answer", "failed": "failed", "canceled": "canceled"}
FAILED_STATUSES = frozenset({"failed", "timeout"})


def display_status(call: Call | None) -> str:
    if call is None:
        return "failed"
    if not call.terminal:
        return "timeout"
    if call.state == "completed":
        return "answered_speech" if call.speech_captured else "answered_no_speech"
    return _TERMINAL_STATUS[call.state]


def row_values(
    contact: ContactRow, call: Call | None, status: str, completed_at: str | None, error: str | None
) -> list[str]:
    return [
        str(contact.row),
        contact.name,
        mask_phone(contact.phone),
        call.id if call else "",
        status,
        (call.transcript or "") if call else "",
        completed_at or "",
        error or "",
    ]
