"""Keyset pagination over UUIDv7 ids (time ordered), newest first."""

from __future__ import annotations

import base64
import uuid
from collections.abc import Callable

from crewquarters_shared.errors import invalid

MAX_LIMIT = 200


def encode_cursor(last_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(last_id.bytes).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> uuid.UUID | None:
    if not cursor:
        return None
    try:
        return uuid.UUID(bytes=base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, TypeError) as exc:
        raise invalid("INVALID_CURSOR", "The pagination cursor is invalid.") from exc


def clamp_limit(limit: int) -> int:
    return max(1, min(MAX_LIMIT, limit))


def page_in_memory[T](
    items: list[T], key: Callable[[T], str], limit: int, cursor: str | None
) -> tuple[list[T], str | None]:
    """Keyset-paginate a small list that comes from another service, ordered by ``key``."""
    limit = clamp_limit(limit)
    ordered = sorted(items, key=key)
    after = decode_text_cursor(cursor)
    if after is not None:
        ordered = [i for i in ordered if key(i) > after]
    page = ordered[:limit]
    more = len(ordered) > limit
    return page, (encode_text_cursor(key(page[-1])) if more and page else None)


def encode_text_cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def decode_text_cursor(cursor: str | None) -> str | None:
    if not cursor:
        return None
    try:
        return base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    except (ValueError, UnicodeDecodeError) as exc:
        raise invalid("INVALID_CURSOR", "The pagination cursor is invalid.") from exc
