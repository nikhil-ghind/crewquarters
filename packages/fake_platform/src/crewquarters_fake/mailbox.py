"""Build Gmail API ``format=full`` messages from compact scenario specs."""

from __future__ import annotations

import base64
import email.utils
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from crewquarters_fake.providers.gmail import CATEGORY_LABELS


def b64url(data: bytes) -> str:
    """Gmail bodies are base64url; strip padding so clients must decode leniently."""
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def resolve_when(spec: dict[str, Any], tz: ZoneInfo, now: datetime) -> datetime:
    if "date" in spec:
        value = datetime.fromisoformat(str(spec["date"]))
        return value if value.tzinfo else value.replace(tzinfo=tz)
    relative = spec.get("relative", {"days": -1, "time": "09:00"})
    local_today: date = now.astimezone(tz).date()
    hour, minute = (int(part) for part in str(relative.get("time", "09:00")).split(":"))
    day = local_today + timedelta(days=int(relative.get("days", -1)))
    return datetime.combine(day, time(hour, minute), tzinfo=tz)


def _text_part(mime_type: str, content: str, charset: str = "utf-8") -> dict[str, Any]:
    data = content.encode(charset, errors="replace")
    return {
        "partId": "",
        "mimeType": mime_type,
        "filename": "",
        "headers": [{"name": "Content-Type", "value": f'{mime_type}; charset="{charset}"'}],
        "body": {"size": len(data), "data": b64url(data)},
    }


def _part(spec: dict[str, Any]) -> dict[str, Any]:
    if spec.get("attachment"):
        return {
            "partId": "",
            "mimeType": spec.get("mimeType", "application/octet-stream"),
            "filename": spec.get("filename", "attachment.bin"),
            "headers": [{"name": "Content-Disposition", "value": "attachment"}],
            "body": {
                "size": int(spec.get("size", 1024)),
                "attachmentId": f"att-{spec.get('filename', 'x')}",
            },
        }
    if "multipart" in spec:
        return {
            "partId": "",
            "mimeType": spec.get("mimeType", "multipart/mixed"),
            "filename": "",
            "headers": [],
            "body": {"size": 0},
            "parts": [_part(child) for child in spec["multipart"]],
        }
    if "html" in spec:
        return _text_part("text/html", str(spec["html"]), spec.get("charset", "utf-8"))
    return _text_part(
        spec.get("mimeType", "text/plain"), str(spec.get("text", "")), spec.get("charset", "utf-8")
    )


def _payload(body: dict[str, Any]) -> dict[str, Any]:
    if "rawPayload" in body:
        return dict(body["rawPayload"])
    if "multipart" in body:
        return {
            **_part(
                {
                    "multipart": body["multipart"],
                    "mimeType": body.get("mimeType", "multipart/mixed"),
                }
            )
        }
    if "text" in body and "html" in body:
        charset = body.get("charset", "utf-8")
        return {
            "partId": "",
            "mimeType": "multipart/alternative",
            "filename": "",
            "headers": [],
            "body": {"size": 0},
            "parts": [
                _text_part("text/plain", body["text"], charset),
                _text_part("text/html", body["html"], charset),
            ],
        }
    return _part(body)


def _snippet(body: dict[str, Any]) -> str:
    text = str(body.get("text") or body.get("html") or "")
    return " ".join(text.split())[:100]


def build_message(spec: dict[str, Any], tz: ZoneInfo, now: datetime) -> dict[str, Any]:
    when = resolve_when(spec, tz, now)
    body = spec.get("body", {"text": ""})
    labels = list(spec.get("labels", ["INBOX"]))
    category = CATEGORY_LABELS.get(
        str(spec.get("category", "personal")).lower(), "CATEGORY_PERSONAL"
    )
    if category not in labels:
        labels.append(category)
    payload = _payload(body)
    if "rawPayload" not in body:
        headers = [
            {"name": "From", "value": spec.get("from", "Sender <sender@example.com>")},
            {"name": "To", "value": spec.get("to", "owner@example.com")},
            {"name": "Subject", "value": spec.get("subject", "")},
        ]
        if not spec.get("omitDateHeader"):
            headers.append({"name": "Date", "value": email.utils.format_datetime(when)})
        payload["headers"] = headers + payload.get("headers", [])
    return {
        "id": spec["id"],
        "threadId": spec.get("threadId", spec["id"]),
        "labelIds": labels,
        "snippet": spec.get("snippet", _snippet(body)),
        "historyId": "1",
        "internalDate": str(spec["internalDate"])
        if "internalDate" in spec
        else str(int(when.timestamp() * 1000)),
        "sizeEstimate": 1024,
        "payload": payload,
    }


def expand_mailbox(
    entries: list[dict[str, Any]], tz: ZoneInfo, now: datetime
) -> list[dict[str, Any]]:
    """Expand ``{generate: {...}}`` entries into individual message specs."""
    specs: list[dict[str, Any]] = []
    for entry in entries:
        generate = entry.get("generate")
        if generate is None:
            specs.append(entry)
            continue
        base = resolve_when(generate, tz, now)
        step = timedelta(minutes=float(generate.get("stepMinutes", 1)))
        for n in range(1, int(generate["count"]) + 1):
            specs.append(
                {
                    "id": f"{generate.get('idPrefix', 'gen-')}{n:03d}",
                    "from": generate.get("from", "Bulk Sender <bulk@example.com>"),
                    "subject": str(generate.get("subject", "Message {n}")).format(n=n),
                    "date": (base + step * (n - 1)).isoformat(),
                    "category": generate.get("category", "personal"),
                    "body": {"text": str(generate.get("body", "Body {n}")).format(n=n)},
                }
            )
    return specs
