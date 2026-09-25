from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from crewctl.testing import run_scenario
from gmail_digest.models import Classification, FetchedMessage
from gmail_digest.reduce import build_digest

AGENT_DIR = Path(__file__).resolve().parents[1]


def test_default_scenario_succeeds(tmp_path: Path) -> None:
    outcome = run_scenario(AGENT_DIR, "default", log_dir=tmp_path)
    assert outcome.state == "SUCCEEDED", outcome.log
    groups = outcome.result["groups"]
    assert [i["messageId"] for i in groups["urgent"]] == ["d1"]
    assert [i["messageId"] for i in groups["important"]] == ["d2"]
    assert [i["messageId"] for i in groups["lowPriority"]] == ["d3"]


def test_message_without_date_still_listed() -> None:
    undated = FetchedMessage(
        "u1", "t1", "S", "No date", None, "text", "https://mail.google.com/mail/u/0/#all/t1"
    )
    digest = build_digest(
        day=date(2026, 9, 23),
        tz=ZoneInfo("UTC"),
        window=(datetime(2026, 9, 23, tzinfo=UTC), datetime(2026, 9, 24, tzinfo=UTC)),
        messages=[undated],
        classifications={"u1": Classification("low", "r", "a", False)},
        truncated=False,
        model={"profile": "local.general.small"},
    )
    [item] = digest.groups.low_priority
    assert (item.message_id, item.received_at) == ("u1", None)
