import base64
from datetime import UTC, datetime
from typing import Any

from crewquarters.google.mime import html_to_text, parse_message, strip_quoted_replies


def b64(text: str, charset: str = "utf-8", pad: bool = False) -> str:
    encoded = base64.urlsafe_b64encode(text.encode(charset)).decode()
    return encoded if pad else encoded.rstrip("=")


def part(mime: str, text: str, charset: str = "utf-8", **extra: Any) -> dict[str, Any]:
    return {
        "mimeType": mime,
        "filename": "",
        "headers": [{"name": "Content-Type", "value": f'{mime}; charset="{charset}"'}],
        "body": {"data": b64(text, charset)},
        **extra,
    }


def message(payload: dict[str, Any], **extra: Any) -> dict[str, Any]:
    headers = [
        {"name": "From", "value": "Ops <ops@example.com>"},
        {"name": "subject", "value": "Server down"},
        {"name": "Date", "value": "Wed, 23 Sep 2026 09:15:00 +0530"},
    ]
    payload = {**payload, "headers": headers + payload.get("headers", [])}
    return {
        "id": "m1",
        "threadId": "t1",
        "labelIds": ["INBOX"],
        "snippet": "The API is down",
        "internalDate": "1790135100000",
        "payload": payload,
        **extra,
    }


def test_plain_text_is_preferred_over_html() -> None:
    raw = message(
        {
            "mimeType": "multipart/alternative",
            "parts": [part("text/html", "<p>HTML</p>"), part("text/plain", "Plain")],
        }
    )
    parsed = parse_message(raw)
    assert parsed.text_body == "Plain"
    assert parsed.sender == "Ops <ops@example.com>"
    assert parsed.subject == "Server down"
    assert parsed.header("SUBJECT") == "Server down"
    assert parsed.thread_id == "t1"
    assert parsed.web_link == "https://mail.google.com/mail/u/0/#all/t1"
    assert parsed.internal_date == datetime(2026, 9, 23, 3, 45, tzinfo=UTC)


def test_html_only_drops_script_style_and_comments() -> None:
    html = (
        "<html><head><title>T</title><style>p{color:red}</style></head><body>"
        "<script>alert('x')</script><!-- hidden -->"
        "<p>Hello&nbsp;<b>there</b></p><div>Line&amp;two</div></body></html>"
    )
    parsed = parse_message(message(part("text/html", html)))
    assert parsed.text_body == "Hello there\n\nLine&two"
    assert (
        "alert" not in parsed.text_body
        and "color" not in parsed.text_body
        and "hidden" not in parsed.text_body
    )


def test_nested_multipart_is_searched() -> None:
    nested = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/related",
                "parts": [{"mimeType": "multipart/alternative", "parts": [part("text/plain", "Deep text")]}],
            },
            {
                "mimeType": "application/pdf",
                "filename": "a.pdf",
                "body": {"attachmentId": "att1", "size": 10},
            },
        ],
    }
    assert parse_message(message(nested)).text_body == "Deep text"


def test_attachment_only_message_has_empty_body() -> None:
    raw = message(
        {
            "mimeType": "multipart/mixed",
            "parts": [{"mimeType": "text/plain", "filename": "notes.txt", "body": {"attachmentId": "a"}}],
        }
    )
    parsed = parse_message(raw)
    assert parsed.text_body == ""
    assert parsed.snippet == "The API is down"


def test_malformed_base64_and_missing_payload_do_not_raise() -> None:
    assert (
        parse_message(message({"mimeType": "text/plain", "body": {"data": "!!!not base64***"}})).text_body
        == ""
    )
    parsed = parse_message({"id": "x", "threadId": "x"})
    assert (parsed.text_body, parsed.subject, parsed.internal_date) == ("", "", None)
    weird = parse_message(
        {"id": "y", "threadId": "y", "payload": {"headers": "nope", "parts": ["bad", None]}}
    )
    assert weird.text_body == ""


def test_padded_and_unpadded_base64_both_decode() -> None:
    padded = {"mimeType": "text/plain", "body": {"data": b64("ab", pad=True)}}
    assert parse_message(message(padded)).text_body == "ab"
    assert parse_message(message({"mimeType": "text/plain", "body": {"data": b64("ab")}})).text_body == "ab"


def test_latin1_and_missing_internal_date() -> None:
    raw = message(part("text/plain", "Café résumé", charset="iso-8859-1"), internalDate="not-a-number")
    parsed = parse_message(raw)
    assert parsed.text_body == "Café résumé"
    assert parsed.internal_date == datetime(2026, 9, 23, 3, 45, tzinfo=UTC)


def test_unknown_charset_falls_back_to_utf8() -> None:
    unknown = part("text/plain", "hello")
    unknown["headers"] = [{"name": "Content-Type", "value": 'text/plain; charset="x-unknown-charset"'}]
    raw = message(unknown)
    assert parse_message(raw).text_body == "hello"


def test_no_internal_date_and_no_date_header_gives_none() -> None:
    raw = message(part("text/plain", "x"))
    raw["payload"]["headers"] = [h for h in raw["payload"]["headers"] if h["name"] != "Date"]
    del raw["internalDate"]
    assert parse_message(raw).internal_date is None


def test_oversize_body_is_truncated() -> None:
    parsed = parse_message(message(part("text/plain", "x" * 5000)), max_chars=4000)
    assert len(parsed.text_body) == 4000
    assert parsed.truncated_body is True


def test_depth_bomb_is_bounded() -> None:
    payload: dict[str, Any] = part("text/plain", "too deep")
    for _ in range(50):
        payload = {"mimeType": "multipart/mixed", "parts": [payload]}
    assert parse_message(message(payload)).text_body == ""


def test_strip_quoted_replies_keeps_original_text() -> None:
    text = (
        "Thanks, I'll join the call at 3pm today.\n\n"
        "On Tue, Sep 22, 2026 at 9:00 AM Bob wrote:\n> old text\n> more"
    )
    assert strip_quoted_replies(text) == "Thanks, I'll join the call at 3pm today."
    quoted = "Sounds good to me, see you there.\n> earlier line\nSigned"
    assert strip_quoted_replies(quoted) == "Sounds good to me, see you there.\nSigned"


def test_strip_quoted_replies_leaves_short_replies_alone() -> None:
    text = "Ok\n-----Original Message-----\nLong original body"
    assert strip_quoted_replies(text) == text


def test_html_to_text_survives_broken_markup() -> None:
    assert "text" in html_to_text("<div><p>text<<</div></b><!")
