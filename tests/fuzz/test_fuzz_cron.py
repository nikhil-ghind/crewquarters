"""Schedules: any cron text and timezone name is accepted or rejected with a 4xx, and every
accepted schedule yields strictly increasing future occurrences."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import available_timezones

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crewquarters_shared.cron import next_occurrences, validate_cron, validate_timezone
from crewquarters_shared.errors import PlatformError

pytestmark = pytest.mark.no_db

ZONES = sorted(z for z in available_timezones() if "/" in z and not z.startswith("Etc/"))
FIELD_ATOMS = st.one_of(
    st.just("*"),
    st.integers(-5, 70).map(str),
    st.builds(lambda a, b: f"{a}-{b}", st.integers(-2, 65), st.integers(-2, 65)),
    st.builds(lambda s: f"*/{s}", st.integers(-1, 70)),
    st.builds(lambda a, s: f"{a}/{s}", st.integers(0, 60), st.integers(0, 70)),
    st.sampled_from(["L", "W", "?", "#", "MON", "sun", "JAN", "dec", "5L", "1#3", "15W", "H"]),
    st.text(alphabet="0123456789*/-,#LW?", min_size=1, max_size=6),
)
FIELD = st.lists(FIELD_ATOMS, min_size=1, max_size=3).map(",".join)
CRON_LIKE = st.lists(FIELD, min_size=3, max_size=7).map(" ".join)


def _field(low: int, high: int, names: list[str] | None = None) -> st.SearchStrategy[str]:
    value = st.integers(low, high).map(str)
    atom = st.one_of(
        st.just("*"),
        value,
        st.builds(lambda a, b: f"{min(a, b)}-{max(a, b)}", value.map(int), value.map(int)),
        st.builds(lambda s: f"*/{s}", st.integers(1, high)),
        st.sampled_from(names or ["*"]),
    )
    return st.lists(atom, min_size=1, max_size=3).map(",".join)


# Well-formed five-field expressions (most are valid; some, like February 30, never occur).
CRON = st.tuples(
    _field(0, 59),
    _field(0, 23),
    _field(1, 31),
    _field(1, 12, ["JAN", "feb", "DEC"]),
    _field(0, 6, ["MON", "sun", "FRI"]),
).map(" ".join)


def _accepts_or_rejects(fn: object, *args: object) -> object:
    try:
        return fn(*args)  # type: ignore[operator]
    except PlatformError as exc:
        assert 400 <= exc.status_code < 500
        return None


@given(st.one_of(CRON, CRON_LIKE, st.text(max_size=40)))
def test_cron_text_is_accepted_or_rejected(expr: str) -> None:
    _accepts_or_rejects(validate_cron, expr)


@given(st.one_of(st.sampled_from(ZONES), st.text(max_size=40)))
def test_timezone_name_is_accepted_or_rejected(name: str) -> None:
    _accepts_or_rejects(validate_timezone, name)


@given(
    CRON,
    st.sampled_from(ZONES),
    st.datetimes(min_value=datetime(2000, 1, 1), max_value=datetime(2090, 1, 1)),
)
def test_accepted_schedule_has_increasing_future_occurrences(
    expr: str, zone: str, after_naive: datetime
) -> None:
    normalized = _accepts_or_rejects(validate_cron, expr)
    if normalized is None:
        return
    after = after_naive.replace(tzinfo=UTC)
    occurrences = _accepts_or_rejects(next_occurrences, normalized, zone, after, 3)
    if occurrences is None:
        return
    assert isinstance(occurrences, list)
    previous = after
    for occurrence in occurrences:
        assert occurrence > previous
        assert occurrence.utcoffset() == timedelta(0)
        previous = occurrence
