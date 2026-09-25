"""The laptop demo scenario works end to end (process launcher; e2e covers the Docker path)."""

from pathlib import Path

import yaml
from agent_runs import FIXTURES, assert_result_matches_manifest, launch, prepare

from crewquarters_fake.client import FakePlatformClient

DEMO = FIXTURES / "demo"


def config(agent: str) -> dict:  # type: ignore[type-arg]
    return dict(yaml.safe_load((DEMO / "configs" / f"{agent}.yaml").read_text()))


def test_demo_digest_groups_yesterday(fake_client: FakePlatformClient, tmp_path: Path) -> None:
    manifest, installation = prepare(fake_client, "gmail_digest", "demo", config("gmail_digest"))
    outcome = launch(fake_client, "gmail_digest", manifest, installation, tmp_path)
    assert outcome.state == "SUCCEEDED", outcome.log
    assert_result_matches_manifest(manifest, outcome.result)
    groups = outcome.result["groups"]
    assert [i["messageId"] for i in groups["urgent"]] == ["demo-security", "demo-outage"]
    assert outcome.result["counts"] == {
        "urgent": 2,
        "important": 8,
        "lowPriority": 10,
        "needsReview": 2,
    }
    everything = {i["messageId"] for g in groups.values() for i in g}
    assert (
        not {"demo-promo-1", "demo-promo-2", "demo-promo-3", "demo-two-days-ago", "demo-today"}
        & everything
    )
    assert "demo-injection" in {i["messageId"] for i in groups["lowPriority"]}


def test_demo_caller_places_three_calls(fake_client: FakePlatformClient, tmp_path: Path) -> None:
    manifest, installation = prepare(
        fake_client, "caller", "demo", {**config("caller"), "callPollSeconds": 0.05}
    )
    fake_client.add_auto_answer("confirm-calls-v1:*", {"choice": "approve"})
    outcome = launch(fake_client, "caller", manifest, installation, tmp_path)
    assert outcome.state == "SUCCEEDED", outcome.log
    assert outcome.result["summary"] == {
        "called": 3,
        "answered": 2,
        "responsesCaptured": 1,
        "skipped": 2,
        "failed": 0,
    }
    assert fake_client.state("calls")["byNumber"] == {
        "+15555550101": 1,
        "+15555550102": 1,
        "+15555550103": 1,
    }
