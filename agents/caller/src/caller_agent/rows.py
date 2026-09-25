"""Contact rows and the eligibility rules (spec section 8.3, rule 2)."""

from __future__ import annotations

import itertools
import unicodedata
from dataclasses import dataclass

from crewquarters.telephony import is_e164

CONSENT_YES = frozenset({"yes", "true", "consented"})
DONE_STATUSES = frozenset({"done", "called", "skip", "dnc", "do-not-call"})
READY_STATUSES = frozenset({"", "ready", "pending"})
MAX_NAME = 40
_NAME_PUNCTUATION = frozenset(" '\u2019-.")


def plain_name(name: str) -> bool:
    """The broker speaks only the approved script with ``{name}`` filled in by a plain name
    (``crewquarters_broker.agent_api.plain_name``; keep the two rules identical): 1-40
    characters, starting with a letter, made of letters (with their combining marks),
    spaces, apostrophes, hyphens and periods; no digits or other punctuation; no leading,
    trailing or repeated separators except ". "; and a period only ends the name or follows
    a one-letter initial. A cell that fails is skipped, never sent to the broker."""
    if not 1 <= len(name) <= MAX_NAME or not unicodedata.category(name[0]).startswith("L"):
        return False
    if name != name.strip():
        return False
    if not all(unicodedata.category(c)[0] in "LM" or c in _NAME_PUNCTUATION for c in name):
        return False
    if any(
        a in _NAME_PUNCTUATION and b in _NAME_PUNCTUATION and a + b != ". "
        for a, b in itertools.pairwise(name)
    ):
        return False
    return all(
        i == len(name) - 1 or (i == 1 or name[i - 2] in _NAME_PUNCTUATION)
        for i, c in enumerate(name)
        if c == "."
    )


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
    """Rows of ``name, phone_e164, consent, status``; missing cells empty, blank rows skipped.
    Runs of whitespace in a name collapse to one space."""
    contacts = []
    for offset, cells in enumerate(values):
        name, phone, consent, status = ([str(c).strip() for c in cells] + ["", "", "", ""])[:4]
        name = " ".join(name.split())
        if name or phone or consent or status:
            contacts.append(ContactRow(start_row + offset, name, phone, consent, status))
    return contacts


def _skip_reason(
    contact: ContactRow, seen: set[str], eligible_count: int, max_calls: int
) -> str | None:
    if contact.consent.lower() not in CONSENT_YES:
        return "consent"
    if not is_e164(contact.phone):
        return "invalid_number"
    if not plain_name(contact.name):
        return "invalid_name"
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
