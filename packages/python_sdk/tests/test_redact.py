from crewquarters.redact import mask_phone, redact_text, redact_value


def test_mask_phone_keeps_last_four_digits() -> None:
    assert mask_phone("+14155550123") == "••••0123"
    assert mask_phone("+1 (415) 555-0199") == "••••0199"
    assert mask_phone("12") == "••••"


def test_redact_text_masks_e164_numbers() -> None:
    assert redact_text("calling +14155550123 now") == "calling ••••0123 now"
    assert redact_text("call +1 415 555 0123.") == "call ••••0123."


def test_redact_text_masks_bearer_tokens_and_secret_query_values() -> None:
    assert redact_text("Authorization: Bearer abc.def-ghi") == "Authorization: Bearer [REDACTED]"
    assert redact_text("GET /cb?code=4/0Aabc&state=xyz") == "GET /cb?code=[REDACTED]&state=xyz"
    assert redact_text("url?access_token=secret123 end") == "url?access_token=[REDACTED] end"


def test_redact_text_leaves_epochs_and_ordinary_numbers_alone() -> None:
    text = "after:1727136000 before:1727222400 count=42"
    assert redact_text(text) == text


def test_redact_value_recurses_and_redacts_sensitive_keys() -> None:
    value = {
        "to": "+14155550123",
        "nested": [{"password": "hunter2"}, "Bearer xyz"],
        "apiKey": "sk-123",
        "count": 3,
    }
    assert redact_value(value) == {
        "to": "••••0123",
        "nested": [{"password": "[REDACTED]"}, "Bearer [REDACTED]"],
        "apiKey": "[REDACTED]",
        "count": 3,
    }
