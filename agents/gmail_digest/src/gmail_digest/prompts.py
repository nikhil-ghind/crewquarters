"""Prompts for batch classification. Email content only ever appears inside evidence blocks."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from crewquarters.untrusted import GUARD_INSTRUCTIONS, evidence
from gmail_digest.models import FetchedMessage

SYSTEM = f"""You triage an owner's email into a daily digest.

{GUARD_INSTRUCTIONS}

Classify every evidence block exactly once, using its ref, into one priority:
- urgent: explicit deadlines or meetings within 24 hours, security or account incidents, blocking requests
  from known people, service or payment failures.
- important: needs the owner's attention or a reply soon, but is not urgent.
- low: newsletters, promotions, notifications, and anything informational.

For each item give a one-sentence reason and a short suggested next action. If you are unsure, set
uncertain to true instead of guessing. Never invent deadlines, amounts, or facts that are not in the
evidence. Reply only with JSON that matches the requested schema."""

REPAIR = (
    "Your previous reply did not match the required JSON schema. Reply again with only JSON that "
    "matches the schema, with one item for every evidence ref."
)


def _received(message: FetchedMessage, tz: ZoneInfo) -> str:
    if message.received_at is None:
        return "unknown"
    return datetime.isoformat(message.received_at.astimezone(tz), timespec="minutes")


def _render(message: FetchedMessage, tz: ZoneInfo) -> str:
    header = f"From: {message.sender}\nSubject: {message.subject}\nReceived: {_received(message, tz)}"
    return f"{header}\n\n{message.text}"


def batch_prompt(refs: dict[str, FetchedMessage], boundary: str, tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    blocks = [
        evidence(_render(message, tz), ref=ref, source="gmail", boundary=boundary)
        for ref, message in refs.items()
    ]
    return f"Classify these {len(refs)} emails (refs {', '.join(refs)}).\n\n" + "\n\n".join(blocks)
