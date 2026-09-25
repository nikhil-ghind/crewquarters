"""Turn a Gmail API ``format=full`` message into bounded, plain-text evidence.

Email is untrusted content (PLAN.md section 9.3): the agent gets text only, never HTML,
scripts, or attachment bytes. ``text/plain`` is preferred; HTML is reduced to text.
Malformed parts are skipped rather than failing the whole message.
"""

from __future__ import annotations

import base64
import binascii
import re
from html.parser import HTMLParser
from typing import Any

HEADERS = ("from", "to", "cc", "subject", "date")
_BLOCK_TAGS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table"}
_SKIP_TAGS = {"script", "style", "head", "title"}
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_BLANK_LINES = re.compile(r"\n{3,}")
_SPACES = re.compile(r"[ \t\u00a0]+")
_CHARSET = re.compile(r"charset=\"?([A-Za-z0-9_.:-]+)", re.IGNORECASE)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts)


def _normalize(text: str) -> str:
    text = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _decode(part: dict[str, Any]) -> str | None:
    data = (part.get("body") or {}).get("data")
    if not data:
        return None
    try:
        raw = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        return None
    content_type = next(
        (
            h.get("value", "")
            for h in part.get("headers") or []
            if h.get("name", "").lower() == "content-type"
        ),
        "",
    )
    match = _CHARSET.search(content_type)
    try:
        return raw.decode(match.group(1) if match else "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _walk(
    part: dict[str, Any], plain: list[str], html: list[str], attachments: list[dict[str, Any]]
) -> None:
    mime = str(part.get("mimeType", "")).lower()
    body = part.get("body") or {}
    if part.get("filename"):
        attachments.append(
            {"filename": str(part["filename"])[:200], "mimeType": mime, "size": body.get("size")}
        )
    elif mime == "text/plain":
        if (text := _decode(part)) is not None:
            plain.append(text)
    elif mime == "text/html" and (text := _decode(part)) is not None:
        html.append(text)
    for child in part.get("parts") or []:
        if isinstance(child, dict):
            _walk(child, plain, html, attachments)


def sanitize(message: dict[str, Any], max_chars: int) -> dict[str, Any]:
    payload = message.get("payload") or {}
    headers = {
        str(h.get("name", "")).lower(): str(h.get("value", ""))[:1000]
        for h in payload.get("headers") or []
    }
    plain: list[str] = []
    html: list[str] = []
    attachments: list[dict[str, Any]] = []
    _walk(payload, plain, html, attachments)
    body = _normalize("\n\n".join(plain) if plain else html_to_text("\n\n".join(html)))
    thread_id = str(message.get("threadId", ""))
    return {
        "id": str(message.get("id", "")),
        "threadId": thread_id,
        "labelIds": list(message.get("labelIds") or []),
        "internalDate": message.get("internalDate"),
        **{name: headers.get(name) for name in HEADERS},
        "snippet": _normalize(html_to_text(str(message.get("snippet", "")))),
        "body": body[:max_chars],
        "bodyTruncated": len(body) > max_chars,
        "attachments": attachments,
        "link": f"https://mail.google.com/mail/#all/{thread_id}",
    }
