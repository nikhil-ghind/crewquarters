"""Gmail users.messages list/get stand-in with a small query grammar."""

from __future__ import annotations

import copy
import email.utils
import re
from dataclasses import dataclass, field
from typing import Any

from crewquarters_fake.errors import ApiError

CATEGORY_LABELS = {
    "primary": "CATEGORY_PERSONAL",
    "personal": "CATEGORY_PERSONAL",
    "promotions": "CATEGORY_PROMOTIONS",
    "social": "CATEGORY_SOCIAL",
    "updates": "CATEGORY_UPDATES",
    "forums": "CATEGORY_FORUMS",
}
_TOKEN_RE = re.compile(r"^(-?)(after|before|category|label):(\S+)$")
PAGE_SIZE = 100


@dataclass
class Query:
    after: int | None = None
    before: int | None = None
    include: set[str] = field(default_factory=set)
    exclude: set[str] = field(default_factory=set)


def _label(kind: str, value: str) -> str:
    if kind == "category":
        if value.lower() not in CATEGORY_LABELS:
            raise ValueError(f"unknown category {value}")
        return CATEGORY_LABELS[value.lower()]
    return value.upper()


def parse_query(q: str) -> Query:
    query = Query()
    for token in q.split():
        match = _TOKEN_RE.match(token)
        if match is None:
            raise ValueError(
                f"unsupported query token {token!r} (fake supports after/before/category/label)"
            )
        negated, kind, value = match.groups()
        if kind in {"after", "before"}:
            if negated or not value.isdigit():
                raise ValueError(f"{kind}: needs epoch seconds")
            setattr(query, kind, int(value))
        else:
            (query.exclude if negated else query.include).add(_label(kind, value))
    return query


def _seconds(message: dict[str, Any]) -> float:
    """Gmail indexes every message by date; fall back to the Date header if internalDate is bad."""
    try:
        return int(message.get("internalDate", "0")) / 1000
    except (TypeError, ValueError):
        pass
    headers = message.get("payload", {}).get("headers", [])
    for header in headers if isinstance(headers, list) else []:
        if isinstance(header, dict) and str(header.get("name", "")).lower() == "date":
            try:
                return email.utils.parsedate_to_datetime(str(header.get("value"))).timestamp()
            except (TypeError, ValueError):
                return 0.0
    return 0.0


class GmailProvider:
    def __init__(self) -> None:
        self.messages: dict[str, dict[str, Any]] = {}

    def load(self, messages: list[dict[str, Any]]) -> None:
        self.messages = {m["id"]: m for m in messages}

    def list(
        self,
        q: str = "",
        max_results: int = 100,
        page_token: str | None = None,
        label_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            query = parse_query(q)
        except ValueError as exc:
            raise ApiError(400, "INVALID_REQUEST", str(exc)) from exc
        required = {label.upper() for label in label_ids or []} | query.include
        matches = []
        for message in self.messages.values():
            seconds = _seconds(message)
            labels = {label.upper() for label in message.get("labelIds", [])}
            if query.after is not None and seconds < query.after:
                continue
            if query.before is not None and seconds >= query.before:
                continue
            if not required <= labels or labels & query.exclude:
                continue
            matches.append(message)
        matches.sort(key=_seconds, reverse=True)
        start = int(page_token) if page_token and page_token.isdigit() else 0
        size = max(1, min(max_results, PAGE_SIZE))
        page = matches[start : start + size]
        body: dict[str, Any] = {
            "messages": [{"id": m["id"], "threadId": m["threadId"]} for m in page],
            "resultSizeEstimate": len(matches),
        }
        if start + size < len(matches):
            body["nextPageToken"] = str(start + size)
        return body

    def get(self, message_id: str) -> dict[str, Any]:
        message = self.messages.get(message_id)
        if message is None:
            raise ApiError(404, "NOT_FOUND", f"message {message_id} not found")
        return copy.deepcopy(message)
