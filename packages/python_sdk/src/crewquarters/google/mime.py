"""Safe Gmail message parsing (spec section 5.3): never raises on malformed input."""

from __future__ import annotations

import base64
import binascii
import email.utils
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

MAX_DEPTH = 10
_SKIP_TAGS = frozenset({"script", "style", "head", "title", "noscript", "template"})
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "br", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6",
        "blockquote", "section", "article", "header", "footer", "hr", "pre",
    }
)  # fmt: skip
_B64_CLEAN_RE = re.compile(r"[^A-Za-z0-9_\-+/]")
_REPLY_MARKER_RE = re.compile(r"^On .+ wrote:$")


@dataclass(frozen=True)
class GmailMessage:
    id: str
    thread_id: str
    label_ids: tuple[str, ...]
    snippet: str
    internal_date: datetime | None
    headers: Mapping[str, str]
    sender: str
    subject: str
    text_body: str
    truncated_body: bool
    web_link: str

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


def decode_b64url(data: str) -> bytes:
    cleaned = _B64_CLEAN_RE.sub("", data).replace("+", "-").replace("/", "_")
    try:
        return base64.urlsafe_b64decode(cleaned + "=" * (-len(cleaned) % 4))
    except (binascii.Error, ValueError):
        return b""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def _normalise(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
        text = "".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]*>", " ", html)
    return _normalise(text)


def _walk(part: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    if not isinstance(part, dict):
        return
    yield part
    if depth >= MAX_DEPTH:
        return
    children = part.get("parts")
    if isinstance(children, list):
        for child in children:
            yield from _walk(child, depth + 1)


def _part_headers(part: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    raw = part.get("headers")
    if isinstance(raw, list):
        for header in raw:
            if (
                isinstance(header, dict)
                and isinstance(header.get("name"), str)
                and isinstance(header.get("value"), str)
            ):
                headers.setdefault(header["name"].lower(), header["value"])
    return headers


def _charset(part: dict[str, Any]) -> str:
    match = re.search(r'charset="?([^";\s]+)"?', _part_headers(part).get("content-type", ""), re.IGNORECASE)
    return match.group(1) if match else "utf-8"


def _body(part: dict[str, Any]) -> dict[str, Any]:
    body = part.get("body")
    return body if isinstance(body, dict) else {}


def _is_attachment(part: dict[str, Any]) -> bool:
    body = _body(part)
    disposition = _part_headers(part).get("content-disposition", "").lower()
    return bool(part.get("filename")) or disposition.startswith("attachment") or "data" not in body


def _decode_part(part: dict[str, Any]) -> str:
    data = _body(part).get("data")
    if not isinstance(data, str):
        return ""
    raw = decode_b64url(data)
    try:
        return raw.decode(_charset(part), errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def extract_text(payload: Any) -> str:
    parts = list(_walk(payload))

    def first(mime: str) -> dict[str, Any] | None:
        for part in parts:
            mime_type = str(part.get("mimeType", "")).lower()
            if mime_type.startswith(mime) and not _is_attachment(part):
                return part
        return None

    plain = first("text/plain")
    if plain is not None:
        return _normalise(_decode_part(plain))
    html = first("text/html")
    if html is not None:
        return html_to_text(_decode_part(html))
    return ""


def strip_quoted_replies(text: str) -> str:
    """Drop quoted history when at least 20 characters of the new message remain."""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if _REPLY_MARKER_RE.match(stripped) or stripped == "-----Original Message-----":
            break
        if stripped.startswith(">"):
            continue
        kept.append(line)
    result = "\n".join(kept).strip()
    return result if len(result) >= 20 else text


def _internal_date(raw: dict[str, Any], headers: Mapping[str, str]) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(raw["internalDate"]) / 1000, UTC)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(headers.get("date", ""))
    except (TypeError, ValueError, IndexError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_message(raw: dict[str, Any], *, max_chars: int = 4000) -> GmailMessage:
    raw_payload = raw.get("payload")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    headers = _part_headers(payload)
    try:
        text = extract_text(payload)
    except Exception:
        text = ""
    thread_id = str(raw.get("threadId") or raw.get("id") or "")
    labels = raw.get("labelIds")
    return GmailMessage(
        id=str(raw.get("id", "")),
        thread_id=thread_id,
        label_ids=tuple(str(label) for label in labels) if isinstance(labels, list) else (),
        snippet=str(raw.get("snippet", "")),
        internal_date=_internal_date(raw, headers),
        headers=headers,
        sender=headers.get("from", ""),
        subject=headers.get("subject", ""),
        text_body=text[:max_chars],
        truncated_body=len(text) > max_chars,
        web_link=f"https://mail.google.com/mail/u/0/#all/{thread_id}",
    )
