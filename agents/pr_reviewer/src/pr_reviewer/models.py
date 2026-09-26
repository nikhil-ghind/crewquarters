"""Model output shapes and the run result (camelCase over the wire)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from pr_reviewer.config import CamelModel

Category = Literal["bug", "style"]
Severity = Literal["high", "medium", "low"]


class Finding(BaseModel):
    """One model finding. ``line`` is a new-file line number from the numbered diff."""

    line: int = Field(ge=1)
    category: Category
    severity: Severity
    comment: str = Field(min_length=1, max_length=600)
    suggestion: str | None = Field(None, max_length=400)


class FileReview(BaseModel):
    ref: str
    findings: list[Finding] = Field(default_factory=list, max_length=20)


class ReviewBatch(BaseModel):
    files: list[FileReview]


class ReviewedFinding(CamelModel):
    path: str
    line: int
    category: Category
    severity: Severity
    comment: str
    suggestion: str | None = None


class PullResult(CamelModel):
    number: int
    title: str
    author: str
    url: str
    head_sha: str
    status: Literal["reviewed", "skipped"]
    skip_reason: str | None = None
    files_reviewed: int = 0
    files_skipped: int = 0
    truncated: bool = False
    findings: list[ReviewedFinding] = Field(default_factory=list)
    dropped_findings: int = 0
    posted: bool = False
    review_url: str | None = None
    post_note: str | None = None


class Counts(CamelModel):
    pulls: int
    reviewed: int
    skipped: int
    findings: int
    bugs: int
    style: int
    posted: int


class ModelInfo(CamelModel):
    profile: str
    provider: str | None = None
    model: str | None = None
    locality: str | None = None


class ReviewResult(CamelModel):
    repo: str
    dry_run: bool
    counts: Counts
    pulls: list[PullResult]
    model: ModelInfo
