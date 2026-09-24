from __future__ import annotations

from datetime import UTC, datetime

import pytest

from crewquarters_shared.cron import (
    latest_occurrence_at_or_before,
    next_occurrence,
    next_occurrences,
    validate_cron,
    validate_timezone,
)
from crewquarters_shared.errors import PlatformError

pytestmark = pytest.mark.no_db


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    ("timezone", "after", "expected"),
    [
        ("Asia/Kolkata", utc(2026, 9, 24, 3, 0), utc(2026, 9, 24, 4, 30)),
        ("America/New_York", utc(2026, 9, 24, 15, 0), utc(2026, 9, 25, 14, 0)),
        ("Europe/London", utc(2026, 12, 1, 0, 0), utc(2026, 12, 1, 10, 0)),
        ("UTC", utc(2026, 9, 24, 10, 0), utc(2026, 9, 25, 10, 0)),
    ],
)
def test_ten_am_in_three_zones(timezone: str, after: datetime, expected: datetime) -> None:
    assert next_occurrence("0 10 * * *", timezone, after) == expected


def test_spring_forward_gap_fires_at_next_valid_instant() -> None:
    # 2026-03-08 02:30 does not exist in New York; clocks jump 02:00 -> 03:00 EDT.
    assert next_occurrence("30 2 * * *", "America/New_York", utc(2026, 3, 8, 5)) == utc(
        2026, 3, 8, 7
    )
    # The next day is normal again.
    assert next_occurrence("30 2 * * *", "America/New_York", utc(2026, 3, 8, 7)) == utc(
        2026, 3, 9, 6, 30
    )


def test_fall_back_overlap_fires_once() -> None:
    # 01:30 happens twice on 2026-11-01 in New York; fire at the first (EDT, 05:30Z) only.
    first = next_occurrence("30 1 * * *", "America/New_York", utc(2026, 11, 1, 4))
    assert first == utc(2026, 11, 1, 5, 30)
    assert next_occurrence("30 1 * * *", "America/New_York", first) == utc(2026, 11, 2, 6, 30)


def test_hourly_during_fall_back_does_not_double_fire() -> None:
    times = next_occurrences("0 * * * *", "Europe/London", utc(2026, 10, 24, 23, 30), 4)
    assert len(set(times)) == 4 and times == sorted(times)


def test_latest_missed_occurrence() -> None:
    assert latest_occurrence_at_or_before(
        "0 * * * *", "UTC", utc(2026, 9, 24, 10), utc(2026, 9, 24, 15, 20)
    ) == utc(2026, 9, 24, 15)


@pytest.mark.parametrize("zone", ["IST", "EST", "PST8PDT", "Etc/GMT+5", "Mars/Olympus", ""])
def test_rejects_non_iana_zones(zone: str) -> None:
    with pytest.raises(PlatformError) as err:
        validate_timezone(zone)
    assert err.value.code == "INVALID_TIMEZONE"


@pytest.mark.parametrize("expr", ["* * * *", "60 * * * *", "0 0 * * * *", "banana"])
def test_rejects_bad_cron(expr: str) -> None:
    with pytest.raises(PlatformError):
        validate_cron(expr)


def test_normalizes_cron_whitespace() -> None:
    assert validate_cron("  0   10 * *  * ") == "0 10 * * *"
