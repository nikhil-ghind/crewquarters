"""GitHub pull-request stand-in: list pulls, list files and reviews, post a COMMENT review."""

from __future__ import annotations

import copy
import re
from typing import Any

from crewquarters_fake.errors import ApiError

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def right_side_lines(patch: str) -> set[int]:
    """New-file line numbers a review comment may target: added and unchanged lines in a hunk."""
    lines: set[int] = set()
    current = 0
    for raw in patch.splitlines():
        header = _HUNK.match(raw)
        if header:
            current = int(header.group(1))
        elif current and raw.startswith("-"):
            continue
        elif current and raw[:1] in ("+", " ", ""):
            lines.add(current)
            current += 1
    return lines


class GitHubProvider:
    def __init__(self) -> None:
        self.pulls: dict[int, dict[str, Any]] = {}
        self.reviews: dict[int, list[dict[str, Any]]] = {}
        self._next_id = 5000

    def load(self, pulls: list[dict[str, Any]]) -> None:
        self.pulls = {int(p["number"]): p for p in pulls}
        self.reviews = {}

    def _pull(self, number: int) -> dict[str, Any]:
        pull = self.pulls.get(number)
        if pull is None:
            raise ApiError(404, "NOT_FOUND", f"pull request {number} not found")
        return pull

    def list_pulls(self, limit: int) -> dict[str, Any]:
        ordered = sorted(
            self.pulls.values(), key=lambda p: str(p.get("updatedAt", "")), reverse=True
        )
        return {
            "pulls": [
                {
                    "number": int(p["number"]),
                    "title": p.get("title", ""),
                    "author": p.get("author", ""),
                    "draft": bool(p.get("draft", False)),
                    "headSha": p.get("sha", ""),
                    "headRef": p.get("headRef", f"feature-{p['number']}"),
                    "baseRef": p.get("baseRef", "main"),
                    "url": f"https://github.com/example/repo/pull/{p['number']}",
                    "updatedAt": p.get("updatedAt", ""),
                    "body": p.get("body", ""),
                }
                for p in ordered[:limit]
            ]
        }

    def list_files(self, number: int) -> dict[str, Any]:
        files = []
        for f in self._pull(number).get("files", []):
            patch = f.get("patch")
            lines = patch.splitlines() if isinstance(patch, str) else []
            files.append(
                {
                    "filename": f["filename"],
                    "status": f.get("status", "modified"),
                    "additions": sum(1 for ln in lines if ln.startswith("+")),
                    "deletions": sum(1 for ln in lines if ln.startswith("-")),
                    "patch": patch,
                }
            )
        return {"files": files, "truncated": False}

    def list_reviews(self, number: int) -> dict[str, Any]:
        self._pull(number)
        return {
            "reviews": [
                {"id": r["id"], "commitId": r["commitId"], "body": r["body"]}
                for r in self.reviews.get(number, [])
            ]
        }

    def create_review(
        self, number: int, commit_id: str, body: str, comments: list[dict[str, Any]]
    ) -> dict[str, Any]:
        pull = self._pull(number)
        valid = {
            f["filename"]: right_side_lines(f["patch"])
            for f in pull.get("files", [])
            if isinstance(f.get("patch"), str)
        }
        for comment in comments:
            if comment["line"] not in valid.get(comment["path"], set()):
                raise ApiError(
                    422,
                    "INVALID_REQUEST",
                    "GitHub rejected the review: a comment is outside the diff.",
                    {"path": comment["path"], "line": comment["line"]},
                )
        self._next_id += 1
        review = {
            "id": self._next_id,
            "commitId": commit_id,
            "body": body,
            "comments": copy.deepcopy(comments),
        }
        self.reviews.setdefault(number, []).append(review)
        return {
            "id": review["id"],
            "url": f"https://github.com/example/repo/pull/{number}#pullrequestreview-{review['id']}",
        }

    def snapshot(self) -> dict[str, Any]:
        """Every review posted, for tests and the admin API."""
        return {str(n): copy.deepcopy(r) for n, r in self.reviews.items()}
