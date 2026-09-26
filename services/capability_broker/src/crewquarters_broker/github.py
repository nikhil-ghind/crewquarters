"""GitHub pull-request operations (read PRs, post a review) with a token from the environment.

The token is ``CQ_GITHUB_TOKEN`` (for example ``gh auth token``): it never reaches an agent.
A token scoped to one repository (a fine-grained token with Pull requests: read and write) is
the safest choice. Agents get exactly four operations, and the route layer restricts them to
the repository the owner put in the installation config:

* list open pull requests, most recently updated first;
* list a pull request's changed files with their patches (untrusted content);
* list existing reviews;
* post one review whose event is always ``COMMENT``. The broker never sends ``APPROVE`` or
  ``REQUEST_CHANGES``, so an agent cannot merge-gate or unblock anything.

Provider bodies are never echoed back in errors (see :func:`provider_error`).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from crewquarters_broker.config import BrokerSettings
from crewquarters_broker.errors import needs_connection, permission_denied, provider_error
from crewquarters_shared.errors import PlatformError, invalid, not_found

API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
FILES_PAGE_SIZE = 100
MAX_FILE_PAGES = 3
MAX_REVIEW_PAGES = 3
NOT_CONNECTED = "GitHub is not connected. Set CQ_GITHUB_TOKEN for the capability broker."


class GitHubConnector:
    def __init__(self, settings: BrokerSettings, http: httpx.AsyncClient) -> None:
        # The fake GitHub accepts any bearer token, so fake mode needs none configured.
        self._token = (
            "fake-token"
            if settings.provider_mode == "fake"
            else (settings.github_token.get_secret_value())
        )
        self.http = http

    @property
    def configured(self) -> bool:
        return bool(self._token)

    async def status(self) -> dict[str, Any]:
        if not self.configured:
            return {"status": "NOT_CONNECTED", "grantedCapabilities": [], "lastCheckedAt": None}
        return {
            "status": "CONNECTED",
            "grantedCapabilities": ["pull_requests.read", "pull_requests.write"],
            "lastCheckedAt": None,
            "detail": "Using the token from CQ_GITHUB_TOKEN.",
        }

    # --- Operations -----------------------------------------------------------------

    async def list_pulls(self, repo: str, limit: int) -> dict[str, Any]:
        data = await self._call(
            "GET",
            f"/repos/{_repo(repo)}/pulls",
            params={"state": "open", "sort": "updated", "direction": "desc", "per_page": limit},
        )
        return {"pulls": [_pull(p) for p in data if isinstance(p, dict)][:limit]}

    async def list_files(self, repo: str, number: int) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        truncated = False
        for page in range(1, MAX_FILE_PAGES + 1):
            batch = await self._call(
                "GET",
                f"/repos/{_repo(repo)}/pulls/{number}/files",
                params={"per_page": FILES_PAGE_SIZE, "page": page},
            )
            files += [_file(f) for f in batch if isinstance(f, dict)]
            if len(batch) < FILES_PAGE_SIZE:
                break
            truncated = page == MAX_FILE_PAGES
        return {"files": files, "truncated": truncated}

    async def list_reviews(self, repo: str, number: int) -> dict[str, Any]:
        reviews: list[dict[str, Any]] = []
        for page in range(1, MAX_REVIEW_PAGES + 1):
            batch = await self._call(
                "GET",
                f"/repos/{_repo(repo)}/pulls/{number}/reviews",
                params={"per_page": FILES_PAGE_SIZE, "page": page},
            )
            reviews += [
                {"id": r.get("id"), "commitId": r.get("commit_id"), "body": r.get("body") or ""}
                for r in batch
                if isinstance(r, dict)
            ]
            if len(batch) < FILES_PAGE_SIZE:
                break
        return {"reviews": reviews}

    async def create_review(
        self,
        repo: str,
        number: int,
        commit_id: str,
        body: str,
        comments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "commit_id": commit_id,
            "body": body,
            "event": "COMMENT",
            "comments": [
                {"path": c["path"], "line": c["line"], "side": "RIGHT", "body": c["body"]}
                for c in comments
            ],
        }
        data = await self._call(
            "POST", f"/repos/{_repo(repo)}/pulls/{number}/reviews", json=payload
        )
        return {"id": data.get("id"), "url": data.get("html_url") or ""}

    # --- Internals ------------------------------------------------------------------

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        token = self._token
        if not token:
            raise needs_connection("github", NOT_CONNECTED)
        headers = {
            "authorization": f"Bearer {token}",
            "accept": "application/vnd.github+json",
            "x-github-api-version": API_VERSION,
            "user-agent": "crewquarters-broker",
        }
        try:
            resp = await self.http.request(method, API_URL + path, headers=headers, **kwargs)
        except httpx.HTTPError:
            raise PlatformError("PROVIDER_UNAVAILABLE", "GitHub is unreachable.", 503) from None
        if resp.status_code == 401:
            raise needs_connection("github", "GitHub rejected the token. Check CQ_GITHUB_TOKEN.")
        if resp.status_code == 404:
            raise not_found("GitHub resource", path.rsplit("/", 1)[-1])
        if resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
            raise provider_error("GitHub", 429)
        if resp.status_code == 422:
            # GitHub refuses a review comment on a line outside the diff.
            raise invalid(
                "INVALID_REQUEST", "GitHub rejected the review; a comment may be outside the diff."
            )
        if resp.status_code == 403:
            raise permission_denied(
                "GitHub refused this request; check the token's repository access.",
                providerStatus=403,
            )
        if resp.status_code >= 400:
            raise provider_error("GitHub", resp.status_code)
        try:
            return resp.json()
        except ValueError:
            return {}


def _repo(repo: str) -> str:
    owner, _, name = repo.partition("/")
    return f"{quote(owner, safe='')}/{quote(name, safe='')}"


def _pull(pull: dict[str, Any]) -> dict[str, Any]:
    head, base = pull.get("head") or {}, pull.get("base") or {}
    return {
        "number": pull.get("number"),
        "title": pull.get("title") or "",
        "author": (pull.get("user") or {}).get("login") or "",
        "draft": bool(pull.get("draft")),
        "headSha": head.get("sha") or "",
        "headRef": head.get("ref") or "",
        "baseRef": base.get("ref") or "",
        "url": pull.get("html_url") or "",
        "updatedAt": pull.get("updated_at") or "",
        "body": pull.get("body") or "",
    }


def _file(file: dict[str, Any]) -> dict[str, Any]:
    return {
        "filename": file.get("filename") or "",
        "status": file.get("status") or "modified",
        "additions": int(file.get("additions") or 0),
        "deletions": int(file.get("deletions") or 0),
        "patch": file.get("patch"),
    }
