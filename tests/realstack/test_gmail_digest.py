"""Daily Gmail digest from its registry image: real OAuth consent through the proxy, manual
and scheduled runs, and Google failures (grant expiry, provider timeout)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

DIGEST = "daily-gmail-digest"
TZ = "Asia/Kolkata"
# The broker's fake Gmail fixtures (crewquarters_broker/fakes.py): eight messages from a day
# ago, of which the promotion is excluded by the digest's default excludeCategories.
DIGESTED = {
    "m-plain",
    "m-multipart",
    "m-html",
    "m-empty",
    "m-attachment",
    "m-injection",
    "m-malformed",
}


def _items(result: dict[str, Any]) -> list[dict[str, Any]]:
    groups = result["groups"]
    return [*groups["urgent"], *groups["important"], *groups["lowPriority"]]


def _expected_window(reference: datetime) -> tuple[str, str, str]:
    """[start, end) of the local calendar day before ``reference`` in TZ, as the agent
    computes it (Asia/Kolkata has no DST, so midnight is exact)."""
    tz = ZoneInfo(TZ)
    day = reference.astimezone(tz).date() - timedelta(days=1)
    start = datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(UTC)
    end = start + timedelta(days=1)
    return day.isoformat(), start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")


def assert_traceable(result: dict[str, Any]) -> None:
    """Every digest item points back at a real Gmail message and thread."""
    items = _items(result)
    assert {i["messageId"] for i in items} == DIGESTED, items
    for item in items:
        assert item["threadId"] == f"t-{item['messageId']}"
        assert item["gmailLink"] == f"https://mail.google.com/mail/u/0/#all/{item['threadId']}"
        assert item["from"].endswith("@example.com") and item["subject"]
    assert result["processedCount"] == len(DIGESTED) and result["truncated"] is False
    counts = result["counts"]
    assert counts["urgent"] + counts["important"] + counts["lowPriority"] == len(DIGESTED)
    assert result["model"]["locality"] == "local"


def test_manual_digest_with_traceable_message_references(owner: Any, google: Any) -> None:
    agent = owner.manifest(DIGEST)
    assert agent["latest"]["image"].split("@")[1] == agent["latest"]["imageDigest"]
    installation = owner.install(DIGEST, {"timezone": TZ})
    assert installation["capabilities"] and "google.gmail.readonly" in installation["capabilities"]
    run = owner.start(installation["id"])
    done = owner.wait_state(run["id"], "SUCCEEDED", timeout=180)
    assert done["trigger"] == "manual"

    result = done["result"]
    assert_traceable(result)
    day, start, end = _expected_window(datetime.fromisoformat(done["createdAt"]))
    assert result["date"] == day and result["timezone"] == TZ
    assert result["window"] == {"startUtc": start, "endUtc": end}
    # Prompt injection in an email body is data, not an instruction: no side effects.
    injected = next(i for i in _items(result) if i["messageId"] == "m-injection")
    assert "+15555550100" not in injected["nextAction"]


def test_scheduled_digest_fires_with_the_schedule_trigger_and_day_window(
    owner: Any, google: Any
) -> None:
    installation = owner.install(DIGEST, {"timezone": TZ})
    schedule = owner.ok(
        owner.post(
            "/api/v1/schedules",
            json={"installationId": installation["id"], "cron": "* * * * *", "timezone": TZ},
        )
    )
    assert schedule["ready"] is True and schedule["nextRunAt"], schedule
    try:
        fired = owner.wait_for(
            lambda: owner.ok(
                owner.get("/api/v1/runs", params={"installationId": installation["id"]})
            )["items"],
            "the schedule to fire",
            timeout=90,
            interval=1,
        )
    finally:
        latest = owner.ok(owner.get(f"/api/v1/schedules/{schedule['id']}"))
        owner.ok(
            owner.http.patch(
                f"/api/v1/schedules/{schedule['id']}",
                json={"version": latest["version"], "enabled": False},
            )
        )
    run = fired[-1]
    assert run["trigger"] == "schedule" and run["scheduleId"] == schedule["id"]
    scheduled_for = datetime.fromisoformat(run["scheduledFor"])
    assert scheduled_for == datetime.fromisoformat(schedule["nextRunAt"])
    assert scheduled_for.second == 0 and scheduled_for.microsecond == 0

    done = owner.wait_state(run["id"], "SUCCEEDED", timeout=180)
    result = done["result"]
    day, start, end = _expected_window(scheduled_for)
    assert result["date"] == day
    assert result["window"] == {"startUtc": start, "endUtc": end}
    assert_traceable(result)


def test_expired_google_grant_needs_reconnect_then_recovers(
    owner: Any, fakes: Any, google: Any
) -> None:
    """Google test-mode grants expire after seven days: the broker marks the connection
    NEEDS_ATTENTION and answers NEEDS_CONNECTION; the digest reports it; reconnecting
    through the OAuth flow makes a retry succeed."""
    installation = owner.install(DIGEST, {"timezone": TZ})
    fakes.expire_google()
    run = owner.start(installation["id"])
    failed = owner.wait_state(run["id"], "FAILED", timeout=180)
    assert failed["error"]["code"] == "GOOGLE_RECONNECT_REQUIRED", failed["error"]
    assert "reconnect Google" in failed["error"]["message"]
    connection = owner.wait_for(
        lambda: (c := owner.connection("google"))["status"] == "NEEDS_ATTENTION" and c,
        "the Google connection to need attention",
        10,
    )
    assert connection["detail"]
    audit = owner.ok(owner.get("/api/v1/audit-events", params={"limit": 50}))["items"]
    assert any(e["action"] == "connection.google.expired" for e in audit)

    # The owner reconnects (both scopes, as the Connections page does), then starts again.
    for scope in ("gmail.readonly", "spreadsheets"):
        assert owner.connect_google(scope).headers["location"].endswith("result=connected")
    owner.wait_for(
        lambda: owner.connection("google")["status"] == "CONNECTED", "Google reconnected", 10
    )
    again = owner.start(installation["id"])
    assert_traceable(owner.wait_state(again["id"], "SUCCEEDED", timeout=180)["result"])


def test_google_timeout_is_provider_unavailable_and_retry_recovers(
    owner: Any, fakes: Any, google: Any
) -> None:
    installation = owner.install(DIGEST, {"timezone": TZ})
    fakes.faults(googleApi="timeout")
    run = owner.start(installation["id"])
    failed = owner.wait_state(run["id"], "FAILED", timeout=180)
    assert failed["error"]["code"] == "PROVIDER_UNAVAILABLE", failed["error"]
    assert failed["retryable"] is True
    # A provider outage is not an expired grant: the connection stays CONNECTED.
    assert owner.connection("google")["status"] == "CONNECTED"

    fakes.faults()
    retried = owner.retry(run["id"])
    assert retried["currentAttempt"] == 2
    done = owner.wait_state(run["id"], "SUCCEEDED", timeout=180)
    assert_traceable(done["result"])
