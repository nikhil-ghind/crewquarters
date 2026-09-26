"""Result rows (one per called contact) and contact status updates in the owner's sheet."""

from __future__ import annotations

from caller_agent.rows import ContactRow
from crewquarters.redact import mask_phone
from voice_caller.config import VoiceCallerConfig
from voice_caller.outcome import CallOutcome

HEADER = [
    "source_row",
    "name",
    "phone_masked",
    "call_id",
    "disposition",
    "interest",
    "callback",
    "notes",
    "duration_seconds",
    "completed_at",
]

# What the contact's status cell becomes, so a later run does not call them again. Unanswered
# calls leave the status alone: the contact stays eligible for another attempt.
STATUS_AFTER: dict[str, str] = {
    "dnc": "dnc",
    "completed": "called",
    "declined": "called",
    "callback": "called",
    "wrong_person": "called",
}


def row_values(
    contact: ContactRow,
    call_id: str,
    outcome: CallOutcome,
    duration_seconds: int | None,
    completed_at: str,
) -> list[str]:
    return [
        str(contact.row),
        contact.name,
        mask_phone(contact.phone),
        call_id,
        outcome.disposition,
        outcome.interest,
        "yes" if outcome.callback_requested else "no",
        outcome.notes,
        "" if duration_seconds is None else str(duration_seconds),
        completed_at,
    ]


def result_range(config: VoiceCallerConfig, row: int) -> str:
    """The result row sits at the contact's own row number, so a rewrite is idempotent."""
    return f"{config.result_tab}!A{row}:J{row}"


def status_range(config: VoiceCallerConfig, row: int) -> str:
    return f"{config.input_tab}!D{row}"
