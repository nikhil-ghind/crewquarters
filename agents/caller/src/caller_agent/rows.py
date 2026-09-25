"""Contact rows and the eligibility rules (spec section 8.3, rule 2)."""

from __future__ import annotations

from dataclasses import dataclass

from crewquarters.telephony import is_e164

CONSENT_YES = frozenset({"yes", "true", "consented"})
DONE_STATUSES = frozenset({"done", "called", "skip", "dnc", "do-not-call"})
READY_STATUSES = frozenset({"", "ready", "pending"})


@dataclass(frozen=True)
class ContactRow:
    row: int
    name: str
    phone: str
    consent: str
    status: str


@dataclass(frozen=True)
class Skipped:
    row: int
    name: str
    reason: str


@dataclass(frozen=True)
class Plan:
    eligible: list[ContactRow]
    skipped: list[Skipped]


def read_rows(values: list[list[str]], start_row: int) -> list[ContactRow]:
    """Rows of ``name, phone_e164, consent, status``; missing cells are empty and blank rows are ignored."""
    contacts = []
    for offset, cells in enumerate(values):
        name, phone, consent, status = ([str(c).strip() for c in cells] + ["", "", "", ""])[:4]
        if name or phone or consent or status:
            contacts.append(ContactRow(start_row + offset, name, phone, consent, status))
    return contacts


def _skip_reason(contact: ContactRow, seen: set[str], eligible_count: int, max_calls: int) -> str | None:
    if contact.consent.lower() not in CONSENT_YES:
        return "consent"
    if not is_e164(contact.phone):
        return "invalid_number"
    status = contact.status.lower()
    if status in DONE_STATUSES:
        return "status"
    if status not in READY_STATUSES:
        return "unrecognized_status"
    if contact.phone in seen:
        return "duplicate"
    if eligible_count >= max_calls:
        return "over_cap"
    return None


def classify(contacts: list[ContactRow], max_calls: int) -> Plan:
    eligible: list[ContactRow] = []
    skipped: list[Skipped] = []
    seen: set[str] = set()
    for contact in contacts:
        reason = _skip_reason(contact, seen, len(eligible), max_calls)
        if reason is None:
            eligible.append(contact)
            seen.add(contact.phone)
        else:
            skipped.append(Skipped(contact.row, contact.name, reason))
    return Plan(eligible, skipped)
