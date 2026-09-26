"""GitHub pull-request client (broker-proxied). ``create_review`` is never retried."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crewquarters._transport import BrokerClient


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    author: str
    draft: bool
    head_sha: str
    head_ref: str
    base_ref: str
    url: str
    updated_at: str
    body: str


@dataclass(frozen=True)
class PullFile:
    filename: str
    status: str
    additions: int
    deletions: int
    patch: str | None


@dataclass(frozen=True)
class Review:
    id: int
    commit_id: str | None
    body: str


@dataclass(frozen=True)
class ReviewComment:
    """An inline comment on the new side of the diff, at ``line`` of ``path``."""

    path: str
    line: int
    body: str


@dataclass(frozen=True)
class CreatedReview:
    id: int
    url: str


class GitHubClient:
    def __init__(self, transport: BrokerClient) -> None:
        self._transport = transport

    async def list_pulls(self, repo: str, *, limit: int = 10) -> list[PullRequest]:
        data = await self._transport.request(
            "GET",
            "/github/pulls",
            operation="github.list",
            idempotent=True,
            params={"repo": repo, "limit": limit},
        )
        return [_pull(p) for p in data.get("pulls", [])]

    async def list_files(self, repo: str, number: int) -> tuple[list[PullFile], bool]:
        """The changed files and whether the list was cut short by the broker's cap."""
        data = await self._transport.request(
            "GET",
            f"/github/pulls/{int(number)}/files",
            operation="github.files",
            idempotent=True,
            params={"repo": repo},
        )
        files = [
            PullFile(
                filename=str(f["filename"]),
                status=str(f.get("status", "modified")),
                additions=int(f.get("additions", 0)),
                deletions=int(f.get("deletions", 0)),
                patch=f.get("patch"),
            )
            for f in data.get("files", [])
        ]
        return files, bool(data.get("truncated"))

    async def list_reviews(self, repo: str, number: int) -> list[Review]:
        data = await self._transport.request(
            "GET",
            f"/github/pulls/{int(number)}/reviews",
            operation="github.reviews",
            idempotent=True,
            params={"repo": repo},
        )
        return [
            Review(int(r["id"]), r.get("commitId"), str(r.get("body", "")))
            for r in data.get("reviews", [])
        ]

    async def create_review(
        self,
        repo: str,
        number: int,
        *,
        commit_id: str,
        body: str,
        comments: list[ReviewComment],
    ) -> CreatedReview:
        """Post one ``COMMENT`` review. Never retried: a lost reply raises ``OutcomeUnknown``
        rather than risk a duplicate, so check ``list_reviews`` before trying again."""
        payload: dict[str, Any] = {
            "repo": repo,
            "commitId": commit_id,
            "body": body,
            "comments": [{"path": c.path, "line": c.line, "body": c.body} for c in comments],
        }
        data = await self._transport.request(
            "POST",
            f"/github/pulls/{int(number)}/reviews",
            operation="github.review",
            idempotent=False,
            json=payload,
        )
        return CreatedReview(int(data["id"]), str(data.get("url", "")))


def _pull(p: dict[str, Any]) -> PullRequest:
    return PullRequest(
        number=int(p["number"]),
        title=str(p.get("title", "")),
        author=str(p.get("author", "")),
        draft=bool(p.get("draft")),
        head_sha=str(p.get("headSha", "")),
        head_ref=str(p.get("headRef", "")),
        base_ref=str(p.get("baseRef", "")),
        url=str(p.get("url", "")),
        updated_at=str(p.get("updatedAt", "")),
        body=str(p.get("body", "")),
    )
