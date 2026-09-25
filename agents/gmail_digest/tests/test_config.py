import pytest
from pydantic import ValidationError

from gmail_digest.config import DigestConfig


def test_defaults() -> None:
    config = DigestConfig.model_validate({"timezone": "Asia/Kolkata"})
    assert config.max_messages == 200
    assert config.exclude_categories == ["CATEGORY_PROMOTIONS"]
    assert config.model_profile == "local.general.small"
    assert config.batch_size == 10
    assert config.max_chars_per_message == 4000
    assert config.target_date is None


@pytest.mark.parametrize("zone", ["Asia/Kolkata", "Asia/Calcutta", "UTC", "America/New_York", "Etc/UTC"])
def test_timezone_aliases_and_abbreviations(zone: str) -> None:
    assert DigestConfig.model_validate({"timezone": zone}).timezone == zone


@pytest.mark.parametrize("zone", ["IST", "EST", "EST5EDT", "PST8PDT", "GMT", "Mars/Olympus", ""])
def test_abbreviations_and_unknown_zones_are_rejected(zone: str) -> None:
    with pytest.raises(ValidationError) as info:
        DigestConfig.model_validate({"timezone": zone})
    assert "IANA" in str(info.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [("maxMessages", 0), ("maxMessages", 501), ("batchSize", 26), ("excludeCategories", ["CATEGORY_SPAM"])],
)
def test_bounds(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        DigestConfig.model_validate({"timezone": "UTC", field: value})


def test_target_date_parses() -> None:
    config = DigestConfig.model_validate({"timezone": "UTC", "targetDate": "2026-09-20"})
    assert config.target_date is not None and config.target_date.isoformat() == "2026-09-20"
