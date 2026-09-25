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


# --- Strict redaction for material that leaves the device (diagnostics bundles) ----------
#
# ``redact_text`` above keeps log lines readable. Diagnostics bundles are sent to other
# people, so ``scrub_text`` additionally removes email addresses, provider API keys and
# tokens, OAuth codes and query-string secrets, cookies, database URL passwords, national
# phone numbers, and long random-looking strings. Run IDs (UUIDs) are kept.

_SENSITIVE_NAME = (
    r"[\w.-]*(?:pass(?:word|wd)?|secret|token|api[_-]?key|authorization|cookie|"
    r"auth[_-]?code|private[_-]?key|master[_-]?key|credential|signature)[\w.-]*"
)
_STRICT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Authorization schemes, before the key/value rule sees "Authorization:".
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/=]{8,}"), "Basic [REDACTED]"),
    # Cookie headers: everything after the header name.
    (re.compile(r"(?i)\b((?:set-)?cookie)(\"?\s*[:=]\s*)[^\r\n]*"), r"\1\2[REDACTED]"),
    # "key": "value", key=value, key: value, where the key names a secret.
    (
        re.compile(
            rf"(?i)(\"?{_SENSITIVE_NAME}\"?\s*[:=]\s*)(?!\[REDACTED)"
            r"(\"[^\"]*\"|'[^']*'|[^\s,;&}\]]+)"
        ),
        r"\1[REDACTED]",
    ),
    # OAuth authorization codes and state, and secrets in query strings.
    (
        re.compile(
            r"(?i)([?&;](?:code|state|access_token|refresh_token|id_token|client_secret|"
            r"key|sig|signature)=)[^&\s\"'#]+"
        ),
        r"\1[REDACTED]",
    ),
    # Passwords in connection URLs.
    (re.compile(r"(://[^:/\s@]+:)[^@\s/]+@"), r"\1[REDACTED]@"),
    # Provider credentials by shape.
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "[REDACTED-API-KEY]"),
    (re.compile(r"\b(?:AC|SK)[0-9a-fA-F]{32}\b"), "[REDACTED-TWILIO-ID]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "[REDACTED-API-KEY]"),
    (re.compile(r"\bya29\.[0-9A-Za-z._-]+"), "[REDACTED-TOKEN]"),
    (re.compile(r"\b1//[0-9A-Za-z_-]{16,}"), "[REDACTED-TOKEN]"),
    (re.compile(r"\b(?:gh[pousr]|xox[abprs])[-_][0-9A-Za-z-]{10,}"), "[REDACTED-TOKEN]"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"),
        "[REDACTED-JWT]",
    ),
    # Long hex strings: keys, digests, signatures.
    (re.compile(r"(?<![\w-])[0-9a-fA-F]{40,}(?![\w-])"), "[REDACTED-HEX]"),
    # Email addresses.
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
)
_NATIONAL_PHONE = re.compile(r"(?<![\w.:+-])\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?![\w.-])")
_LONG_TOKEN = re.compile(r"(?<![\w-])[A-Za-z0-9_-]{32,}(?![\w-])")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


def _long_token(match: re.Match[str]) -> str:
    text = match.group(0)
    if _UUID.match(text) or not (re.search(r"\d", text) and re.search(r"[A-Za-z]", text)):
        return text  # an ID or a plain identifier, not a random secret
    return "[REDACTED]"


def scrub_text(value: str) -> str:
    """Strict redaction for text that leaves the device (diagnostics bundles)."""
    for pattern, replacement in _STRICT_PATTERNS:
        value = pattern.sub(replacement, value)
    value = _E164.sub(lambda m: mask_phone(m.group(0)), value)
    value = _NATIONAL_PHONE.sub(lambda m: mask_phone(m.group(0)), value)
    return _LONG_TOKEN.sub(_long_token, value)


def scrub(value: Any) -> Any:
    """:func:`redact` with :func:`scrub_text` for strings."""
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if _SECRET_KEYS.search(str(k)) else scrub(v) for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value
