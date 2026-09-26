"""GitHub pull-request connector: capability and repository scoping, COMMENT-only reviews, and
how real GitHub responses are mapped (the token never leaves the broker)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from crewquarters_broker.config import BrokerSettings
from crewquarters_broker.github import GitHubConnector
from crewquarters_shared.errors import PlatformError

SDK = "/internal/v1/sdk/github"
READ = "github.pull_requests.read"
WRITE = "github.pull_requests.write"
CONFIG = {"repo": "example/shop"}
# Built at runtime so no secret-shaped literal sits in the repository.
TOKEN = "-".join(["fixture", "token", "value"])
REVIEW = {
    "repo": "example/shop",
    "commitId": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
    "body": "Automated review",
    "comments": [{"path": "shop/cart.py", "line": 11, "body": "Off by one."}],
}


async def test_reads_only_the_configured_repository(harness: Any) -> None:
    headers = harness.agent([READ], config=CONFIG)
    ok = await harness.client.get(f"{SDK}/pulls", params={"repo": "example/shop"}, headers=headers)
    assert ok.status_code == 200, ok.text
    assert [p["number"] for p in ok.json()["pulls"]] == [101, 102, 103]
    assert ok.json()["pulls"][0]["headSha"].startswith("a1b2c3d4")
    files = await harness.client.get(
        f"{SDK}/pulls/101/files", params={"repo": "example/shop"}, headers=headers
    )
    assert files.json()["files"][0]["filename"] == "shop/cart.py"
    assert files.json()["files"][0]["additions"] > 0 and files.json()["truncated"] is False
    other = await harness.client.get(
        f"{SDK}/pulls", params={"repo": "example/secret"}, headers=headers
    )
    assert other.status_code == 403 and other.json()["error"]["code"] == "PERMISSION_DENIED"


async def test_a_run_without_a_configured_repo_needs_configuration(harness: Any) -> None:
    headers = harness.agent([READ], config={})
    resp = await harness.client.get(
        f"{SDK}/pulls", params={"repo": "example/shop"}, headers=headers
    )
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NEEDS_CONFIGURATION"


async def test_capabilities_are_separate_for_reading_and_posting(harness: Any) -> None:
    reader = harness.agent([READ], config=CONFIG)
    denied = await harness.client.post(f"{SDK}/pulls/101/reviews", json=REVIEW, headers=reader)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "CAPABILITY_DENIED"
    writer = harness.agent([WRITE], config=CONFIG)
    listing = await harness.client.get(
        f"{SDK}/pulls", params={"repo": "example/shop"}, headers=writer
    )
    assert listing.status_code == 403 and listing.json()["error"]["code"] == "CAPABILITY_DENIED"
    nothing = harness.agent(["events.write"], config=CONFIG)
    assert (
        await harness.client.get(f"{SDK}/pulls", params={"repo": "example/shop"}, headers=nothing)
    ).status_code == 403


async def test_a_review_is_recorded_as_a_comment_on_the_right_side(harness: Any) -> None:
    headers = harness.agent([READ, WRITE], config=CONFIG)
    resp = await harness.client.post(f"{SDK}/pulls/101/reviews", json=REVIEW, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["url"].endswith(f"pullrequestreview-{resp.json()['id']}")
    (posted,) = harness.github.reviews[101]
    assert posted["comments"] == [
        {"path": "shop/cart.py", "line": 11, "side": "RIGHT", "body": "Off by one."}
    ]
    listed = await harness.client.get(
        f"{SDK}/pulls/101/reviews", params={"repo": "example/shop"}, headers=headers
    )
    assert listed.json()["reviews"] == [
        {"id": resp.json()["id"], "commitId": REVIEW["commitId"], "body": "Automated review"}
    ]


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"repo": "example/secret"}, 403),
        ({"commitId": "not-a-sha"}, 422),
        ({"comments": [{"path": "a.py", "line": 0, "body": "x"}]}, 422),
        ({"comments": [{"path": "a.py", "line": 1, "body": "x"}] * 51}, 422),
        ({"event": "APPROVE"}, 422),
    ],
)
async def test_review_requests_are_validated_and_cannot_choose_an_event(
    harness: Any, change: dict[str, Any], status: int
) -> None:
    headers = harness.agent([WRITE], config=CONFIG)
    resp = await harness.client.post(
        f"{SDK}/pulls/101/reviews", json={**REVIEW, **change}, headers=headers
    )
    assert resp.status_code == status, resp.text
    assert harness.github.reviews == {}


async def test_live_mode_without_a_token_asks_the_owner_to_connect(live_harness: Any) -> None:
    headers = live_harness.agent([READ], config=CONFIG)
    resp = await live_harness.client.get(
        f"{SDK}/pulls", params={"repo": "example/shop"}, headers=headers
    )
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "NEEDS_CONNECTION"
    assert "CQ_GITHUB_TOKEN" in resp.json()["error"]["message"]
    listing = await live_harness.client.get(
        "/internal/v1/connections", headers=live_harness.service_headers
    )
    github = next(c for c in listing.json() if c["provider"] == "github")
    assert github["status"] == "NOT_CONNECTED" and github["grantedCapabilities"] == []


# --- The connector against a scripted GitHub ------------------------------------------------


def connector(handler: Any) -> tuple[GitHubConnector, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)  # type: ignore[no-any-return]

    settings = BrokerSettings(provider_mode="live", github_token=SecretStr(TOKEN))
    http = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return GitHubConnector(settings, http), seen


async def test_requests_carry_the_token_and_the_api_version_only_to_github() -> None:
    gh, seen = connector(lambda r: httpx.Response(200, json=[]))
    await gh.list_pulls("example/shop", 5)
    (request,) = seen
    assert request.url.host == "api.github.com" and request.url.path == "/repos/example/shop/pulls"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["x-github-api-version"] == "2022-11-28"
    assert dict(request.url.params) == {
        "state": "open",
        "sort": "updated",
        "direction": "desc",
        "per_page": "5",
    }


async def test_create_review_always_sends_event_comment() -> None:
    gh, seen = connector(lambda r: httpx.Response(200, json={"id": 9, "html_url": "https://x/9"}))
    created = await gh.create_review(
        "example/shop", 3, "abc1234", "body", [{"path": "a.py", "line": 2, "body": "c"}]
    )
    assert created == {"id": 9, "url": "https://x/9"}
    sent = json.loads(seen[0].content)
    assert sent == {
        "commit_id": "abc1234",
        "body": "body",
        "event": "COMMENT",
        "comments": [{"path": "a.py", "line": 2, "side": "RIGHT", "body": "c"}],
    }


async def test_file_listing_pages_and_reports_truncation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        return httpx.Response(200, json=[{"filename": f"f{page}-{i}"} for i in range(100)])

    gh, seen = connector(handler)
    result = await gh.list_files("example/shop", 1)
    assert len(result["files"]) == 300 and result["truncated"] is True and len(seen) == 3


@pytest.mark.parametrize(
    ("response", "code", "status"),
    [
        (httpx.Response(401, json={"message": "Bad credentials"}), "NEEDS_CONNECTION", 409),
        (httpx.Response(404, json={"message": "Not Found"}), "NOT_FOUND", 404),
        (
            httpx.Response(403, json={"message": "Resource not accessible"}),
            "PERMISSION_DENIED",
            403,
        ),
        (
            httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={"message": "limit"}),
            "RATE_LIMITED",
            429,
        ),
        (
            httpx.Response(422, json={"message": "line must be part of the diff"}),
            "INVALID_REQUEST",
            422,
        ),
        (httpx.Response(500, json={"message": "secret-internal-detail"}), "PROVIDER_ERROR", 502),
    ],
)
async def test_github_failures_map_to_platform_errors_without_echoing_the_body(
    response: httpx.Response, code: str, status: int
) -> None:
    gh, _ = connector(lambda r: response)
    with pytest.raises(PlatformError) as caught:
        await gh.list_pulls("example/shop", 5)
    assert caught.value.code == code and caught.value.status_code == status
    assert "secret-internal-detail" not in caught.value.message
    assert TOKEN not in caught.value.message


async def test_an_unreachable_github_is_a_provider_outage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    gh, _ = connector(handler)
    with pytest.raises(PlatformError) as caught:
        await gh.list_pulls("example/shop", 5)
    assert caught.value.code == "PROVIDER_UNAVAILABLE" and caught.value.status_code == 503
