"""Redaction helpers: logs and events never carry secrets, tokens, or full phone numbers."""

from __future__ import annotations

import re
from typing import Any

_SECRET_KEYS = re.compile(
    r"(pass(word)?|secret|token|authorization|cookie|api[_-]?key|refresh|auth[_-]?code)",
    re.IGNORECASE,
)
_E164 = re.compile(r"\+\d{8,15}")
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")


def mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return f"***{digits[-4:]}" if len(digits) >= 4 else "***"


def redact_text(value: str) -> str:
    value = _BEARER.sub("Bearer [REDACTED]", value)
    return _E164.sub(lambda m: mask_phone(m.group(0)), value)


def redact(value: Any) -> Any:
    """Recursively redact secret-looking keys and phone numbers in JSON-like data."""
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if _SECRET_KEYS.search(str(k)) else redact(v) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
