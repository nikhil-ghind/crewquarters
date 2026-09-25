from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from gmail_digest.window import day_window, target_date


def hours(day: date, zone: str) -> float:
    start, end = day_window(day, ZoneInfo(zone))
    return (end - start) / timedelta(hours=1)


def test_kolkata_day_is_24_hours_starting_at_local_midnight() -> None:
    start, end = day_window(date(2026, 9, 23), ZoneInfo("Asia/Kolkata"))
    assert start == datetime(2026, 9, 22, 18, 30, tzinfo=UTC)
    assert end == datetime(2026, 9, 23, 18, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("day", "zone", "expected"),
    [
        (date(2026, 3, 8), "America/New_York", 23),
        (date(2026, 11, 1), "America/New_York", 25),
        (date(2026, 10, 4), "Australia/Lord_Howe", 23.5),
        (date(2026, 9, 23), "Asia/Kolkata", 24),
    ],
)
def test_dst_days_have_the_right_length(day: date, zone: str, expected: float) -> None:
    assert hours(day, zone) == expected


def test_nonexistent_midnight_starts_at_the_first_valid_instant() -> None:
    zone = ZoneInfo("America/Santiago")
    start, _ = day_window(date(2026, 9, 6), zone)
    local = start.astimezone(zone)
    assert local.date() == date(2026, 9, 6)
    assert (local.hour, local.minute) == (1, 0)
    assert (start - timedelta(seconds=1)).astimezone(zone).date() == date(2026, 9, 5)


def test_window_boundaries_are_exact_local_date_edges() -> None:
    zone = ZoneInfo("America/New_York")
    start, end = day_window(date(2026, 11, 1), zone)
    assert start.astimezone(zone).date() == date(2026, 11, 1)
    assert (end - timedelta(seconds=1)).astimezone(zone).date() == date(2026, 11, 1)
    assert end.astimezone(zone).date() == date(2026, 11, 2)


def test_year_boundary() -> None:
    zone = ZoneInfo("Asia/Kolkata")
    assert target_date(datetime(2027, 1, 1, 4, 30, tzinfo=UTC), zone, None) == date(2026, 12, 31)


def test_late_scheduled_reference_still_uses_the_scheduled_day() -> None:
    zone = ZoneInfo("Asia/Kolkata")
    scheduled_for = datetime(2026, 9, 24, 4, 30, tzinfo=UTC)
    assert target_date(scheduled_for, zone, None) == date(2026, 9, 23)
    late_but_same_reference = scheduled_for
    assert target_date(late_but_same_reference + timedelta(hours=0), zone, None) == date(
        2026, 9, 23
    )


def test_reference_just_after_local_midnight_picks_the_previous_day() -> None:
    zone = ZoneInfo("Asia/Kolkata")
    assert target_date(datetime(2026, 9, 23, 18, 31, tzinfo=UTC), zone, None) == date(2026, 9, 23)


def test_override_wins() -> None:
    assert target_date(
        datetime(2026, 9, 24, tzinfo=UTC), ZoneInfo("UTC"), date(2026, 1, 2)
    ) == date(2026, 1, 2)
