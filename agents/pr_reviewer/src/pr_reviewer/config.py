"""Reviewer configuration."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

REPO_PATTERN = r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$"


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ReviewConfig(CamelModel):
    repo: str = Field(pattern=REPO_PATTERN)
    max_prs: int = Field(5, ge=1, le=20)
    skip_drafts: bool = True
    # A dry run (the default) reviews and reports but posts nothing to GitHub.
    post_comments: bool = False
    style_guide: str = Field("", max_length=2000)
    model_profile: str = "local.general.small"
    max_files_per_pr: int = Field(30, ge=1, le=100)
    max_diff_chars_per_file: int = Field(12000, ge=500, le=60000)
    max_chars_per_batch: int = Field(16000, ge=2000, le=60000)
    max_comments_per_pr: int = Field(15, ge=1, le=50)
