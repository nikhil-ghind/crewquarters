"""Result models; results serialize with camelCase keys."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# What the model returns. Items cite the short refs p1..pN it was shown, never real citation ids.
class ThemeDraft(CamelModel):
    title: str = Field(max_length=120)
    summary: str = Field(max_length=600)
    citations: list[str]


class HighlightDraft(CamelModel):
    title: str = Field(max_length=160)
    why_it_matters: str = Field(max_length=500)
    next_step: str | None = Field(None, max_length=300)
    citations: list[str]


class SpaceBrief(CamelModel):
    domain: str = Field(max_length=120)
    summary: str = Field(max_length=800)
    themes: list[ThemeDraft]
    highlights: list[HighlightDraft]
    explore_next: list[str] = Field(default_factory=list)


class Expansion(CamelModel):
    queries: list[str]


# What the run returns. Citations here are the platform's real citation ids.
class Theme(CamelModel):
    title: str
    summary: str
    citations: list[str]


class Highlight(CamelModel):
    title: str
    why_it_matters: str
    next_step: str | None
    citations: list[str]


class SourceRef(CamelModel):
    citation_id: str
    document_name: str
    locator: dict[str, Any]
    score: float


class Stats(CamelModel):
    queries_run: int
    passages_considered: int
    passages_used: int
    documents_seen: int


class ModelInfo(CamelModel):
    profile: str
    provider: str | None = None
    model: str | None = None
    locality: str | None = None


class SpaceResult(CamelModel):
    status: Literal["ready", "insufficient_context"]
    domain: str | None
    intent: str | None
    summary: str
    generated_at: datetime
    degraded: bool
    themes: list[Theme] = Field(default_factory=list)
    highlights: list[Highlight] = Field(default_factory=list)
    explore_next: list[str] = Field(default_factory=list)
    suggested_additions: list[str] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    stats: Stats
    model: ModelInfo
