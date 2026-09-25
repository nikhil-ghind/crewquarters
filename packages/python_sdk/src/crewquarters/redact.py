"""Redaction for logs and events: phone numbers, bearer tokens, and secret-looking values."""

from __future__ import annotations

import re
from typing import Any

MASK = "••••"
_PHONE_RE = re.compile(r"\+\d[\d\s().-]{6,18}\d")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_SECRET_PARAM_RE = re.compile(
    r"(?i)\b(token|access_token|refresh_token|api_key|apikey|key|secret|password|code)=([^&\s\"']+)"
)
_SENSITIVE_KEYS = frozenset(
    {
        "token",
        "accesstoken",
        "refreshtoken",
        "apikey",
        "secret",
        "password",
        "authorization",
        "clientsecret",
    }
)


def mask_phone(number: str) -> str:
    digits = re.sub(r"\D", "", number)
    return MASK + digits[-4:] if len(digits) >= 4 else MASK


def redact_text(text: str) -> str:
    text = _PHONE_RE.sub(lambda m: mask_phone(m.group(0)), text)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)}[REDACTED]", text)
    return _SECRET_PARAM_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if isinstance(key, str)
            and key.replace("_", "").replace("-", "").lower() in _SENSITIVE_KEYS
            else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value
