import base64
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from crewquarters_fake.errors import ApiError
from crewquarters_fake.mailbox import build_message
from crewquarters_fake.providers.gmail import GmailProvider

KOLKATA = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def epoch(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp())


def b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode()


def test_build_message_plain_text_with_relative_date() -> None:
    message = build_message(
        {
            "id": "m1",
            "from": "Ops <ops@example.com>",
            "subject": "Server down",
            "relative": {"days": -1, "time": "09:15"},
            "body": {"text": "The API is down."},
        },
        KOLKATA,
        NOW,
    )
    assert message["id"] == "m1"
    assert message["threadId"] == "m1"
    assert message["internalDate"] == str(epoch("2026-09-23T09:15:00+05:30") * 1000)
    assert message["labelIds"] == ["INBOX", "CATEGORY_PERSONAL"]
    headers = {h["name"]: h["value"] for h in message["payload"]["headers"]}
    assert headers["Subject"] == "Server down"
    assert headers["From"] == "Ops <ops@example.com>"
    assert message["payload"]["mimeType"] == "text/plain"
    assert b64(message["payload"]["body"]["data"]) == "The API is down."
    assert message["snippet"] == "The API is down."


def test_build_message_with_text_and_html_is_multipart_alternative() -> None:
    message = build_message(
        {
            "id": "m2",
            "subject": "s",
            "date": "2026-09-23T10:00:00+05:30",
            "body": {"text": "hi", "html": "<p>hi</p>"},
        },
        KOLKATA,
        NOW,
    )
    payload = message["payload"]
    assert payload["mimeType"] == "multipart/alternative"
    assert [p["mimeType"] for p in payload["parts"]] == ["text/plain", "text/html"]


def test_build_message_with_attachment_and_category() -> None:
    message = build_message(
        {
            "id": "m3",
            "subject": "invoice",
            "date": "2026-09-23T10:00:00+05:30",
            "category": "promotions",
            "body": {
                "multipart": [{"filename": "invoice.pdf", "mimeType": "application/pdf", "attachment": True}]
            },
        },
        KOLKATA,
        NOW,
    )
    assert "CATEGORY_PROMOTIONS" in message["labelIds"]
    [part] = message["payload"]["parts"]
    assert part["filename"] == "invoice.pdf"
    assert "attachmentId" in part["body"]


def test_raw_payload_and_internal_date_override_pass_through() -> None:
    message = build_message(
        {
            "id": "m4",
            "internalDate": "not-a-number",
            "body": {"rawPayload": {"mimeType": "text/plain", "body": {}}},
        },
        KOLKATA,
        NOW,
    )
    assert message["internalDate"] == "not-a-number"
    assert message["payload"] == {"mimeType": "text/plain", "body": {}}


def mailbox(count: int) -> GmailProvider:
    gmail = GmailProvider()
    messages = []
    for i in range(count):
        messages.append(
            build_message(
                {
                    "id": f"m{i:03d}",
                    "subject": f"Message {i}",
                    "date": f"2026-09-23T{i % 24:02d}:{i % 60:02d}:00+05:30",
                    "category": "promotions" if i % 10 == 0 else "personal",
                    "labels": ["INBOX", "Work"] if i % 7 == 0 else ["INBOX"],
                    "body": {"text": f"body {i}"},
                },
                KOLKATA,
                NOW,
            )
        )
    messages.append(
        build_message(
            {"id": "old", "subject": "old", "date": "2026-09-21T10:00:00+05:30", "body": {"text": "x"}},
            KOLKATA,
            NOW,
        )
    )
    gmail.load(messages)
    return gmail


DAY = f"after:{epoch('2026-09-23T00:00:00+05:30')} before:{epoch('2026-09-24T00:00:00+05:30')}"


def test_list_filters_by_window_and_paginates_at_100() -> None:
    gmail = mailbox(150)
    first = gmail.list(DAY, max_results=500)
    assert len(first["messages"]) == 100
    assert first["resultSizeEstimate"] == 150
    second = gmail.list(DAY, max_results=500, page_token=first["nextPageToken"])
    assert len(second["messages"]) == 50
    assert "nextPageToken" not in second
    ids = [m["id"] for m in first["messages"] + second["messages"]]
    assert "old" not in ids
    assert len(set(ids)) == 150


def test_list_returns_newest_first() -> None:
    ids = [m["id"] for m in mailbox(5).list(DAY)["messages"]]
    assert ids == ["m004", "m003", "m002", "m001", "m000"]


def test_list_excludes_category_and_filters_labels() -> None:
    gmail = mailbox(30)
    without_promotions = gmail.list(f"{DAY} -category:promotions")["messages"]
    assert {m["id"] for m in without_promotions}.isdisjoint({"m000", "m010", "m020"})
    work = gmail.list(f"{DAY} label:work")["messages"]
    assert {m["id"] for m in work} == {"m000", "m007", "m014", "m021", "m028"}
    only_promotions = gmail.list(f"{DAY} category:promotions")["messages"]
    assert {m["id"] for m in only_promotions} == {"m000", "m010", "m020"}


def test_unknown_query_token_is_rejected() -> None:
    with pytest.raises(ApiError) as info:
        mailbox(1).list("from:boss@example.com")
    assert info.value.status == 400


def test_get_returns_a_copy_and_404s() -> None:
    gmail = mailbox(1)
    message = gmail.get("m000")
    message["payload"] = {}
    assert gmail.get("m000")["payload"] != {}
    with pytest.raises(ApiError) as info:
        gmail.get("missing")
    assert info.value.status == 404
