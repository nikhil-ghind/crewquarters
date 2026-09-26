"""PR reviewer end to end against fixture pull requests: dry run, posting, idempotence, and a
hostile diff whose model reply tries to ping people and plant a one-click suggestion."""

from pathlib import Path
from typing import Any

from agent_runs import AGENTS, assert_result_matches_manifest, launch, prepare

from crewquarters_fake.client import FakePlatformClient
from crewquarters_fake.harness import RunOutcome

DEFAULT = str(AGENTS["pr_reviewer"] / "scenarios" / "default")
CONFIG = {"repo": "example/shop", "modelProfile": "local.general.small"}


def review(
    client: FakePlatformClient, tmp_path: Path, config: dict[str, Any], scenario: str = DEFAULT
) -> RunOutcome:
    manifest, installation = prepare(client, "pr_reviewer", scenario, {**CONFIG, **config})
    outcome = launch(client, "pr_reviewer", manifest, installation, tmp_path)
    if outcome.state == "SUCCEEDED":
        assert_result_matches_manifest(manifest, outcome.result)
    return outcome


def pull(outcome: RunOutcome, number: int) -> dict[str, Any]:
    return next(p for p in outcome.result["pulls"] if p["number"] == number)


def test_dry_run_reports_findings_and_posts_nothing(
    fake_client: FakePlatformClient, tmp_path: Path
) -> None:
    outcome = review(fake_client, tmp_path, {})
    assert outcome.state == "SUCCEEDED", outcome.log
    result = outcome.result
    assert result["dryRun"] is True
    assert [p["number"] for p in result["pulls"]] == [101, 102]  # the draft (103) is skipped
    assert result["counts"] == {
        "pulls": 2,
        "reviewed": 2,
        "skipped": 0,
        "findings": 3,
        "bugs": 2,
        "style": 1,
        "posted": 0,
    }
    cart = pull(outcome, 101)
    assert [(f["line"], f["category"]) for f in cart["findings"]] == [(11, "bug"), (16, "bug")]
    assert cart["droppedFindings"] == 1  # the model's line 99 is not in the diff
    assert cart["filesReviewed"] == 1 and cart["filesSkipped"] == 1  # uv.lock is not reviewed
    assert cart["posted"] is False
    assert fake_client.state("reviews") == {}


def test_posting_sends_one_comment_review_per_pr_and_a_rerun_skips_them(
    fake_client: FakePlatformClient, tmp_path: Path
) -> None:
    manifest, installation = prepare(
        fake_client, "pr_reviewer", DEFAULT, {**CONFIG, "postComments": True}
    )
    first = launch(fake_client, "pr_reviewer", manifest, installation, tmp_path)
    assert first.state == "SUCCEEDED", first.log
    assert first.result["counts"]["posted"] == 2
    reviews = fake_client.state("reviews")
    assert set(reviews) == {"101", "102"}
    posted = reviews["101"][0]
    assert posted["commitId"] == "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
    assert "crewquarters-pr-reviewer sha=a1b2c3d4e5f6" in posted["body"]
    assert [(c["path"], c["line"]) for c in posted["comments"]] == [
        ("shop/cart.py", 11),
        ("shop/cart.py", 16),
    ]
    assert posted["comments"][0]["body"].startswith("**Bug · high**")
    assert pull(first, 101)["reviewUrl"].endswith(f"pullrequestreview-{posted['id']}")

    # A second run on the same installation (loading the scenario again would clear the reviews).
    second = launch(fake_client, "pr_reviewer", manifest, installation, tmp_path)
    assert second.state == "SUCCEEDED", second.log
    assert second.result["counts"]["reviewed"] == 0 and second.result["counts"]["posted"] == 0
    assert {p["skipReason"] for p in second.result["pulls"]} == {"Already reviewed at this commit"}
    assert {n: len(r) for n, r in fake_client.state("reviews").items()} == {"101": 1, "102": 1}


def test_max_prs_limits_the_review(fake_client: FakePlatformClient, tmp_path: Path) -> None:
    outcome = review(fake_client, tmp_path, {"maxPrs": 1})
    assert [p["number"] for p in outcome.result["pulls"]] == [101]


def test_drafts_are_reviewed_when_asked(fake_client: FakePlatformClient, tmp_path: Path) -> None:
    outcome = review(fake_client, tmp_path, {"skipDrafts": False, "maxPrs": 3})
    draft = pull(outcome, 103)
    assert draft["status"] == "skipped" and draft["skipReason"] == "No reviewable text changes"


def test_a_missing_connection_fails_with_a_clear_code(
    fake_client: FakePlatformClient, tmp_path: Path
) -> None:
    manifest, installation = prepare(fake_client, "pr_reviewer", DEFAULT, CONFIG)
    fake_client.set_connection("github", "missing")
    outcome = launch(fake_client, "pr_reviewer", manifest, installation, tmp_path)
    assert outcome.state == "FAILED"
    assert "GITHUB_NOT_CONNECTED" in str(outcome.error)


def test_hostile_diff_and_reply_cannot_ping_hide_html_or_plant_a_suggestion(
    fake_client: FakePlatformClient, tmp_path: Path
) -> None:
    outcome = review(
        fake_client,
        tmp_path,
        {"postComments": True},
        str(AGENTS["pr_reviewer"].parents[1] / "tests/fixtures/scenarios/pr-review-injection"),
    )
    assert outcome.state == "SUCCEEDED", outcome.log
    llm = fake_client.state("llm")
    prompt = "\n".join(m["content"] for call in llm for m in call["messages"])
    assert "<<<EVIDENCE ref=f1" in prompt and "IGNORE PREVIOUS INSTRUCTIONS" in prompt
    (posted,) = fake_client.state("reviews")["7"]
    body = posted["comments"][0]["body"]
    assert "@octocat" not in body and "<!--" not in body and "```suggestion" not in body.lower()
    assert "@​octocat" in body
