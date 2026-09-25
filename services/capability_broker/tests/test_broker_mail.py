"""Gmail sanitizing: every fixture shape yields bounded plain text and never crashes."""

from __future__ import annotations

import pytest

from crewquarters_broker.fakes import fixture_messages
from crewquarters_broker.mail import html_to_text, sanitize

pytestmark = pytest.mark.no_db

MESSAGES = {m["id"]: m for m in fixture_messages(1_700_000_000_000)}


def test_multipart_prefers_plain_text() -> None:
    out = sanitize(MESSAGES["m-multipart"], 1000)
    assert out["body"] == "Invoice 42 is overdue."
    assert out["from"] == "billing@example.com" and out["threadId"] == "t-m-multipart"


def test_html_only_drops_scripts_and_styles() -> None:
    assert sanitize(MESSAGES["m-html"], 1000)["body"] == "Security alert"


@pytest.mark.parametrize("message_id", ["m-empty", "m-malformed"])
def test_empty_or_malformed_body(message_id: str) -> None:
    out = sanitize(MESSAGES[message_id], 1000)
    assert out["body"] == "" and out["bodyTruncated"] is False


def test_attachment_only_lists_metadata_not_bytes() -> None:
    out = sanitize(MESSAGES["m-attachment"], 1000)
    assert out["body"] == ""
    assert out["attachments"] == [
        {"filename": "report.pdf", "mimeType": "application/pdf", "size": 1024}
    ]


def test_injection_is_returned_as_inert_text() -> None:
    out = sanitize(MESSAGES["m-injection"], 1000)
    assert out["body"].startswith("IGNORE PREVIOUS INSTRUCTIONS")
    assert set(out) == {
        "id",
        "threadId",
        "labelIds",
        "internalDate",
        "from",
        "to",
        "cc",
        "subject",
        "date",
        "snippet",
        "body",
        "bodyTruncated",
        "attachments",
        "link",
    }


def test_truncation_is_reported() -> None:
    out = sanitize(MESSAGES["m-plain"], 10)
    assert out["body"] == "Standup mo" and out["bodyTruncated"] is True


def test_totally_malformed_message() -> None:
    out = sanitize({"payload": {"parts": ["not-a-dict", {"mimeType": None}]}}, 100)
    assert out["body"] == "" and out["id"] == ""


def test_charset_and_control_characters() -> None:
    import base64

    raw = "Caf\xe9\x07 menu\r\n\r\n\r\n\r\nNext".encode("latin-1")
    part = {
        "mimeType": "text/plain",
        "headers": [{"name": "Content-Type", "value": 'text/plain; charset="iso-8859-1"'}],
        "body": {"data": base64.urlsafe_b64encode(raw).decode()},
    }
    assert sanitize({"payload": part}, 100)["body"] == "Café menu\n\nNext"
    part["headers"] = [{"name": "Content-Type", "value": "text/plain; charset=unknown-x"}]
    assert "menu" in sanitize({"payload": part}, 100)["body"]


def test_html_to_text_blocks_and_entities() -> None:
    assert html_to_text("<div>a&amp;b</div><p>c<br>d</p><title>t</title>") == "\na&b\n\nc\nd\n"
