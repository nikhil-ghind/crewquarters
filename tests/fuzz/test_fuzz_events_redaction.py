"""Run event payloads: schema validation never crashes and redaction always holds.

Agents are untrusted (ADR 0007): every payload is validated against the run-event schema
and redacted before it is stored or logged. These properties check that no payload makes
either step raise, and that redaction leaves no secret-keyed value, bearer token, or full
E.164 number behind.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crewquarters_shared.config import get_settings
from crewquarters_shared.errors import PlatformError
from crewquarters_shared.logs import JsonFormatter
from crewquarters_shared.redaction import mask_phone, redact, redact_text
from crewquarters_shared.runs.events import event_validator
from crewquarters_shared.runs.service import AGENT_EVENT_TYPES, MAX_EVENT_BYTES
from crewquarters_shared.schema_guard import check_size

pytestmark = pytest.mark.no_db

PHONE = st.from_regex(r"\+[1-9][0-9]{7,14}", fullmatch=True)
TOKEN = st.from_regex(r"[A-Za-z0-9._~+/=-]{1,40}", fullmatch=True)
SECRET_KEYS = st.sampled_from(
    [
        "password",
        "apiKey",
        "api_key",
        "authorization",
        "Cookie",
        "refreshToken",
        "secret",
        "authCode",
    ]
)
# Text that is likely to contain the shapes redaction looks for.
TEXT = st.one_of(
    st.text(max_size=60),
    st.builds(lambda a, p, b: f"{a}{p}{b}", st.text(max_size=10), PHONE, st.text(max_size=10)),
    st.builds(lambda a, t: f"{a}Bearer {t}", st.text(max_size=10), TOKEN),
)
KEYS = st.one_of(st.text(max_size=15), SECRET_KEYS)
SCALARS = (
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**63), max_value=2**63)
    | st.floats(allow_nan=False, allow_infinity=False)
    | TEXT
)
JSON = st.recursive(
    SCALARS,
    lambda children: st.lists(children, max_size=4) | st.dictionaries(KEYS, children, max_size=4),
    max_leaves=30,
)
_SECRET_KEY = re.compile(
    r"(pass(word)?|secret|token|authorization|cookie|api[_-]?key|refresh|auth[_-]?code)", re.I
)
_UNREDACTED_BEARER = re.compile(r"(?i)bearer\s+(?!\[REDACTED\])[A-Za-z0-9._~+/=-]+")
_FULL_PHONE = re.compile(r"\+\d{8,15}")


def _leaks(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)) and item != "[REDACTED]":
                found.append(f"key {key!r}")
            found.extend(_leaks(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_leaks(item))
    elif isinstance(value, str):
        found.extend(m.group(0) for m in _UNREDACTED_BEARER.finditer(value))
        found.extend(m.group(0) for m in _FULL_PHONE.finditer(value))
    return found


@given(JSON)
def test_redaction_leaves_no_secret(value: Any) -> None:
    redacted = redact(value)
    assert _leaks(redacted) == []
    assert redact(redacted) == redacted  # idempotent: re-redacting stored events is safe
    json.dumps(redacted)  # still JSON


@given(st.text(max_size=200))
def test_redact_text_never_raises_and_masks(text: str) -> None:
    assert not _FULL_PHONE.search(redact_text(text))


@given(st.text(max_size=40))
def test_mask_phone_keeps_at_most_four_digits(value: str) -> None:
    masked = mask_phone(value)
    assert masked.startswith("***") and sum(c.isdigit() for c in masked) <= 4


@given(st.sampled_from(sorted(AGENT_EVENT_TYPES)), st.dictionaries(KEYS, JSON, max_size=6))
def test_event_schema_validation_never_raises(event_type: str, payload: dict[str, Any]) -> None:
    validator = event_validator(get_settings().contracts_dir)
    envelope = {
        "eventId": "0190c0de-0000-7000-8000-000000000001",
        "runId": "0190c0de-0000-7000-8000-000000000002",
        "sequence": 1,
        "type": event_type,
        "occurredAt": "2026-09-25T10:00:00Z",
        "payload": payload,
    }
    list(validator.iter_errors(envelope))


@given(JSON)
def test_size_check_accepts_or_rejects(payload: Any) -> None:
    try:
        check_size(payload, MAX_EVENT_BYTES, "EVENT_TOO_LARGE", "The event payload")
    except PlatformError as exc:
        assert exc.code == "EVENT_TOO_LARGE"


@given(st.integers(min_value=1, max_value=900))
def test_deeply_nested_payload_is_redacted_or_rejected(depth: int) -> None:
    """``json.loads`` accepts nesting up to the interpreter's recursion limit; redaction
    and the size check must cope with anything the parser returns."""
    payload: Any = "Bearer secret-token"
    for _ in range(depth):
        payload = {"x": [payload]}
    try:
        check_size(payload, MAX_EVENT_BYTES, "EVENT_TOO_LARGE", "The event payload")
    except PlatformError:
        return
    assert _leaks(redact(payload)) == []


@given(st.dictionaries(KEYS, JSON, max_size=5), TEXT)
def test_log_lines_are_redacted(fields: dict[str, Any], message: str) -> None:
    import logging

    record = logging.LogRecord("t", logging.INFO, __file__, 1, message, None, None)
    record.fields = fields
    line = json.loads(JsonFormatter("test").format(record))
    assert not _UNREDACTED_BEARER.search(line["message"])
    assert not _FULL_PHONE.search(line["message"])
    assert _leaks(line.get("fields", {})) == []
