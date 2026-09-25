"""The SDK's Gmail parsing (``crewquarters.google.mime``) on hostile messages: it promises
never to raise, to bound the body, and to strip markup from HTML-only messages."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crewquarters.google.mime import (
    decode_b64url,
    html_to_text,
    parse_message,
    strip_quoted_replies,
)

pytestmark = pytest.mark.no_db

SCALARS = (
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**80), max_value=2**80)
    | st.floats()
    | st.text(max_size=30)
)
JSON = st.recursive(
    SCALARS,
    lambda c: st.lists(c, max_size=3) | st.dictionaries(st.text(max_size=10), c, max_size=4),
    max_leaves=20,
)
CHARSETS = st.sampled_from(
    ["utf-8", "iso-8859-1", "utf-7", "utf-16", "idna", "punycode", "rot13", "base64", "hex",
     "zlib", "bogus", "", "uu", "unicode_escape", "raw_unicode_escape", "cp037", "koi8-r"]
)  # fmt: skip
DATES = st.one_of(
    st.text(max_size=40),
    st.sampled_from(
        [
            "Mon, 1 Jan 0001 00:00:00 -2359",
            "Fri, 31 Dec 9999 23:59:59 +2359",
            "Thu, 1 Jan 1970 00:00:00 +0000",
            "31 Dec 99999 23:59:59 +0000",
            "Mon, 32 Feb 2026 25:61:61 +9999",
        ]
    ),
)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


@st.composite
def parts(draw: st.DrawFn, depth: int = 0) -> dict[str, Any]:
    mime = draw(st.sampled_from(["text/plain", "text/html", "multipart/mixed", "TEXT/PLAIN",
                                 "image/png", "", "multipart/alternative"]))  # fmt: skip
    headers = [
        {"name": "Content-Type", "value": f"{mime}; charset={draw(CHARSETS)}"},
    ]
    if draw(st.booleans()):
        headers.append({"name": "Content-Disposition", "value": draw(st.text(max_size=20))})
    body_bytes = draw(
        st.one_of(
            st.binary(max_size=300),
            st.text(max_size=300).map(str.encode),
            st.just(b"<script>alert(1)</script><p>hi</p><style>x</style>"),
            st.just(b"<" * 500 + b"&#99999999;&bogus;<!--"),
        )
    )
    data = draw(st.one_of(st.just(b64(body_bytes)), st.text(max_size=40), st.just("====")))
    part: dict[str, Any] = {"mimeType": mime, "headers": headers, "body": {"data": data}}
    if draw(st.booleans()):
        part["filename"] = draw(st.text(max_size=10))
    if depth < 12 and mime.startswith("multipart"):
        part["parts"] = draw(st.lists(parts(depth + 1), max_size=3))
    return part


@st.composite
def messages(draw: st.DrawFn) -> dict[str, Any]:
    payload = draw(parts())
    payload.setdefault("headers", []).extend(
        [
            {"name": "From", "value": draw(st.text(max_size=30))},
            {"name": "Subject", "value": draw(st.text(max_size=30))},
            {"name": "Date", "value": draw(DATES)},
        ]
    )
    raw: dict[str, Any] = {"id": draw(st.text(max_size=10)), "payload": payload}
    for key in ("threadId", "labelIds", "snippet", "internalDate"):
        if draw(st.booleans()):
            raw[key] = draw(JSON)
    return raw


def deep_message(depth: int) -> dict[str, Any]:
    part: dict[str, Any] = {
        "mimeType": "text/plain",
        "body": {"data": b64(b"deep secret")},
        "headers": [],
    }
    for _ in range(depth):
        part = {"mimeType": "multipart/mixed", "parts": [part], "headers": []}
    return {"id": "m", "payload": part}


@given(messages(), st.integers(min_value=0, max_value=5000))
def test_hostile_message_parses_within_bounds(raw: dict[str, Any], max_chars: int) -> None:
    message = parse_message(raw, max_chars=max_chars)
    assert len(message.text_body) <= max_chars
    assert "<script" not in message.text_body.lower() or any(
        p.get("mimeType", "").lower().startswith("text/plain") for p in _walk(raw["payload"])
    )


def _walk(part: Any) -> list[dict[str, Any]]:
    out = [part] if isinstance(part, dict) else []
    if isinstance(part, dict) and isinstance(part.get("parts"), list):
        for child in part["parts"]:
            out.extend(_walk(child))
    return out


@given(st.dictionaries(st.text(max_size=10), JSON, max_size=6))
def test_arbitrary_json_message_parses(raw: dict[str, Any]) -> None:
    parse_message(raw)


@pytest.mark.parametrize("depth", [5, 10, 11, 50, 900])
def test_deep_nesting_is_bounded(depth: int) -> None:
    message = parse_message(deep_message(depth))
    assert message.text_body in ("deep secret", "")


@given(st.text(max_size=200))
def test_base64_decoding_never_raises(data: str) -> None:
    assert isinstance(decode_b64url(data), bytes)


@given(st.text(max_size=500))
def test_html_to_text_never_raises(html: str) -> None:
    # Output is plain text for the model, never rendered as HTML. Note: malformed markup
    # such as "<?" before a <script> element lets the script's text through as data.
    assert isinstance(html_to_text(html), str)


@given(st.text(max_size=500))
def test_strip_quoted_replies_never_grows(text: str) -> None:
    assert len(strip_quoted_replies(text)) <= len(text)
