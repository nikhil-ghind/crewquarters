"""Previous-local-calendar-day window, exact across DST gaps and repeated hours."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def target_date(reference: datetime, tz: ZoneInfo, override: date | None) -> date:
    """The day to digest: the configured override, else the local date before ``reference``."""
    if override is not None:
        return override
    return reference.astimezone(tz).date() - timedelta(days=1)


def day_start(day: date, tz: ZoneInfo) -> datetime:
    """The earliest UTC instant whose local date in ``tz`` is ``day``.

    ``fold=0`` picks the earlier of two repeated midnights; for a nonexistent midnight Python maps
    the wall time with the pre-transition offset, which lands on the transition instant itself. The
    loop below walks back if a zone's rules ever produce a later instant than the real day start.
    """
    start = datetime.combine(day, time(0, 0), tzinfo=tz).astimezone(UTC)
    while (start - timedelta(minutes=15)).astimezone(tz).date() == day:
        start -= timedelta(minutes=15)
    return start


def day_window(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """``[start, end)`` in UTC covering every instant whose local date is ``day``."""
    return day_start(day, tz), day_start(day + timedelta(days=1), tz)
