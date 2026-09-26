"""Broker GitHub pull-request routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, Field

from crewquarters_fake.broker.audit import audited, require_connection
from crewquarters_fake.broker.auth import RunAuth, require, run_auth
from crewquarters_fake.errors import ApiError

router = APIRouter()
REPO_PATTERN = r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$"
READ = "github.pull_requests.read"
WRITE = "github.pull_requests.write"


class CommentIn(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    line: int = Field(ge=1)
    body: str = Field(min_length=1, max_length=5000)


class ReviewIn(BaseModel):
    repo: str = Field(pattern=REPO_PATTERN)
    commitId: str = Field(pattern=r"^[0-9a-f]{7,64}$")
    body: str = Field(max_length=20000)
    comments: list[CommentIn] = Field(max_length=50)


def _scoped(auth: RunAuth, repo: str) -> None:
    """Like the real broker: only the repository the owner configured."""
    configured = auth.installation.config.get("repo")
    if not isinstance(configured, str) or not configured:
        raise ApiError(409, "NEEDS_CONFIGURATION", "The installation config has no repo.")
    if repo != configured:
        raise ApiError(403, "PERMISSION_DENIED", "Only the configured repo may be used.")


@router.get("/github/pulls")
async def github_list_pulls(
    request: Request,
    repo: str = Query(pattern=REPO_PATTERN),
    limit: int = Query(10, ge=1, le=50),
    auth: RunAuth = Depends(run_auth),
) -> dict[str, Any]:
    require(auth, READ, "broker.github.list")
    _scoped(auth, repo)
    require_connection(auth, "github")

    async def call() -> dict[str, Any]:
        return auth.store.github.list_pulls(limit)

    return await audited(auth, request, "github.pull_requests", "broker.github.list", call)


@router.get("/github/pulls/{number}/files")
async def github_list_files(
    request: Request,
    number: int = Path(ge=1),
    repo: str = Query(pattern=REPO_PATTERN),
    auth: RunAuth = Depends(run_auth),
) -> dict[str, Any]:
    require(auth, READ, "broker.github.files")
    _scoped(auth, repo)
    require_connection(auth, "github")

    async def call() -> dict[str, Any]:
        return auth.store.github.list_files(number)

    return await audited(auth, request, "github.pull_requests", "broker.github.files", call)


@router.get("/github/pulls/{number}/reviews")
async def github_list_reviews(
    request: Request,
    number: int = Path(ge=1),
    repo: str = Query(pattern=REPO_PATTERN),
    auth: RunAuth = Depends(run_auth),
) -> dict[str, Any]:
    require(auth, READ, "broker.github.reviews")
    _scoped(auth, repo)
    require_connection(auth, "github")

    async def call() -> dict[str, Any]:
        return auth.store.github.list_reviews(number)

    return await audited(auth, request, "github.pull_requests", "broker.github.reviews", call)


@router.post("/github/pulls/{number}/reviews")
async def github_create_review(
    body: ReviewIn,
    request: Request,
    number: int = Path(ge=1),
    auth: RunAuth = Depends(run_auth),
) -> dict[str, Any]:
    require(auth, WRITE, "broker.github.review")
    _scoped(auth, body.repo)
    require_connection(auth, "github")

    async def call() -> dict[str, Any]:
        comments = [c.model_dump() for c in body.comments]
        return auth.store.github.create_review(number, body.commitId, body.body, comments)

    return await audited(auth, request, "github.pull_requests", "broker.github.review", call)
