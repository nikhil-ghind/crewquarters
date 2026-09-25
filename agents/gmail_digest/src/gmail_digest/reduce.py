"""Reduce step: deterministic grouping, so every item maps to a fetched message."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from gmail_digest.models import (
    Classification,
    DigestCounts,
    DigestGroups,
    DigestItem,
    DigestResult,
    DigestWindow,
    FetchedMessage,
    ModelInfo,
)

GROUP_FOR = {"urgent": "urgent", "important": "important", "low": "low_priority"}


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_digest(
    *,
    day: date,
    tz: ZoneInfo,
    window: tuple[datetime, datetime],
    messages: list[FetchedMessage],
    classifications: dict[str, Classification],
    truncated: bool,
    model: dict[str, str | None],
) -> DigestResult:
    groups: dict[str, list[tuple[datetime | None, DigestItem]]] = {
        "urgent": [],
        "important": [],
        "low_priority": [],
    }
    for message in messages:
        verdict = classifications[message.id]
        item = DigestItem(
            message_id=message.id,
            thread_id=message.thread_id,
            sender=message.sender,
            subject=message.subject,
            received_at=message.received_at.astimezone(tz).isoformat()
            if message.received_at
            else None,
            reason=verdict.reason,
            next_action=verdict.next_action,
            needs_review=verdict.needs_review,
            gmail_link=message.web_link,
        )
        groups[GROUP_FOR[verdict.priority]].append((message.received_at, item))

    oldest = datetime.min.replace(tzinfo=UTC)

    def ordered(entries: list[tuple[datetime | None, DigestItem]]) -> list[DigestItem]:
        return [item for _, item in sorted(entries, key=lambda e: e[0] or oldest, reverse=True)]

    result_groups = DigestGroups(
        urgent=ordered(groups["urgent"]),
        important=ordered(groups["important"]),
        low_priority=ordered(groups["low_priority"]),
    )
    return DigestResult(
        date=day.isoformat(),
        timezone=str(tz),
        window=DigestWindow(start_utc=_utc(window[0]), end_utc=_utc(window[1])),
        processed_count=len(messages),
        truncated=truncated,
        counts=DigestCounts(
            urgent=len(result_groups.urgent),
            important=len(result_groups.important),
            low_priority=len(result_groups.low_priority),
            needs_review=sum(1 for c in classifications.values() if c.needs_review),
        ),
        groups=result_groups,
        model=ModelInfo.model_validate(model),
    )
