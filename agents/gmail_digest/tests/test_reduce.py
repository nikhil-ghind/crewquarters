from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from gmail_digest.models import Classification, FetchedMessage
from gmail_digest.reduce import build_digest


def message(i: int, hour: int | None) -> FetchedMessage:
    return FetchedMessage(
        id=f"id{i}",
        thread_id=f"t{i}",
        sender=f"S{i}",
        subject=f"Subject {i}",
        received_at=datetime(2026, 9, 23, hour, 0, tzinfo=UTC) if hour is not None else None,
        text="",
        web_link=f"https://mail.google.com/mail/u/0/#all/t{i}",
    )


def test_groups_sort_and_count() -> None:
    messages = [message(1, 3), message(2, 5), message(3, 4), message(4, None), message(5, 1)]
    classifications = {
        "id1": Classification("urgent", "r1", "a1", False),
        "id2": Classification("urgent", "r2", "a2", False),
        "id3": Classification("low", "r3", "a3", False),
        "id4": Classification("important", "r4", "a4", True),
        "id5": Classification("important", "r5", "a5", False),
    }
    tz = ZoneInfo("Asia/Kolkata")
    digest = build_digest(
        day=date(2026, 9, 23),
        tz=tz,
        window=(datetime(2026, 9, 22, 18, 30, tzinfo=UTC), datetime(2026, 9, 23, 18, 30, tzinfo=UTC)),
        messages=messages,
        classifications=classifications,
        truncated=True,
        model={"profile": "local.general.small", "provider": "mock-local", "model": "m", "locality": "local"},
    )
    assert [i.message_id for i in digest.groups.urgent] == ["id2", "id1"]
    assert [i.message_id for i in digest.groups.important] == ["id5", "id4"]
    assert [i.message_id for i in digest.groups.low_priority] == ["id3"]
    assert (
        digest.counts.urgent,
        digest.counts.important,
        digest.counts.low_priority,
        digest.counts.needs_review,
    ) == (
        2,
        2,
        1,
        1,
    )
    assert digest.processed_count == 5
    assert digest.truncated is True
    dumped = digest.model_dump(mode="json", by_alias=True)
    assert dumped["window"] == {"startUtc": "2026-09-22T18:30:00Z", "endUtc": "2026-09-23T18:30:00Z"}
    assert dumped["groups"]["urgent"][0]["receivedAt"] == "2026-09-23T10:30:00+05:30"
    assert dumped["groups"]["urgent"][0]["from"] == "S2"
    assert dumped["groups"]["important"][1]["receivedAt"] is None
    assert set(dumped["groups"]) == {"urgent", "important", "lowPriority"}
