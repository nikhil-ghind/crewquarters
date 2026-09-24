"""Timezone-aware cron evaluation (PLAN.md section 7.1).

Rules:
* Only IANA zone names (``Asia/Kolkata``, ``UTC``). Abbreviations such as ``IST`` are rejected.
* Cron fields are evaluated against local wall-clock time in the schedule's zone.
* A nonexistent local time (spring-forward gap) fires at the next valid instant,
  which is the first wall-clock minute after the gap.
* A repeated local time (fall-back overlap) fires once, at its first occurrence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

from croniter import CroniterBadDateError, croniter

from crewquarters_shared.errors import invalid


@lru_cache
def _zones() -> frozenset[str]:
    return frozenset(available_timezones())


def validate_timezone(name: str) -> ZoneInfo:
    is_iana = name == "UTC" or ("/" in name and not name.startswith("Etc/") and name in _zones())
    if not is_iana:
        raise invalid(
            "INVALID_TIMEZONE",
            "Use an IANA timezone name such as Asia/Kolkata; abbreviations are not accepted.",
            timezone=name,
        )
    return ZoneInfo(name)


def validate_cron(expr: str) -> str:
    expr = " ".join(expr.split())
    if len(expr.split(" ")) != 5 or not croniter.is_valid(expr):
        raise invalid("INVALID_CRON", "Cron must have five fields: minute hour day month weekday.")
    try:  # syntactically valid but impossible, e.g. "0 0 30 2 *" (February 30)
        croniter(expr, datetime(2000, 1, 1)).get_next(datetime)
    except CroniterBadDateError as exc:
        raise invalid("INVALID_CRON", "This schedule never occurs.") from exc
    return expr


def _is_valid_local(naive: datetime, zone: ZoneInfo) -> bool:
    aware = naive.replace(tzinfo=zone, fold=0)
    return aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == naive


def _resolve_local(naive: datetime, zone: ZoneInfo) -> datetime:
    """Map a local wall-clock time to UTC, applying the gap and overlap rules."""
    probe = naive
    for _ in range(24 * 60):
        if _is_valid_local(probe, zone):
            return probe.replace(tzinfo=zone, fold=0).astimezone(UTC)
        probe = probe.replace(second=0, microsecond=0) + timedelta(minutes=1)
    raise invalid("INVALID_SCHEDULE", "Could not resolve a valid local time.")


def next_occurrence(expr: str, timezone: str, after: datetime) -> datetime:
    """Return the first occurrence strictly after ``after`` (aware), as UTC."""
    zone = validate_timezone(timezone)
    after_utc = after.astimezone(UTC)
    local_start = after_utc.astimezone(zone).replace(tzinfo=None)
    it = croniter(expr, local_start)
    for _ in range(10_000):
        try:
            naive = it.get_next(datetime)
        except CroniterBadDateError as exc:
            raise invalid("INVALID_CRON", "This schedule never occurs.") from exc
        candidate = _resolve_local(naive, zone)
        if candidate > after_utc:
            return candidate
    raise invalid("INVALID_SCHEDULE", "Schedule produced no future occurrence.")


def next_occurrences(expr: str, timezone: str, after: datetime, count: int) -> list[datetime]:
    out: list[datetime] = []
    cursor = after
    for _ in range(count):
        cursor = next_occurrence(expr, timezone, cursor)
        out.append(cursor)
    return out


def latest_occurrence_at_or_before(
    expr: str, timezone: str, start: datetime, now: datetime
) -> datetime:
    """Latest occurrence in ``[start, now]``; ``start`` must itself be an occurrence."""
    latest = start
    for _ in range(100_000):
        nxt = next_occurrence(expr, timezone, latest)
        if nxt > now:
            return latest
        latest = nxt
    return latest
