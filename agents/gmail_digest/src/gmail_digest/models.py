"""Digest data models; results serialize with camelCase keys (``from`` for the sender)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

Priority = Literal["urgent", "important", "low"]


@dataclass(frozen=True)
class FetchedMessage:
    id: str
    thread_id: str
    sender: str
    subject: str
    received_at: datetime | None
    text: str
    web_link: str


@dataclass(frozen=True)
class Classification:
    priority: Priority
    reason: str
    next_action: str
    needs_review: bool


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class DigestItem(CamelModel):
    message_id: str
    thread_id: str
    sender: str = Field(alias="from")
    subject: str
    received_at: str | None
    reason: str
    next_action: str
    needs_review: bool
    gmail_link: str


class DigestGroups(CamelModel):
    urgent: list[DigestItem] = Field(default_factory=list)
    important: list[DigestItem] = Field(default_factory=list)
    low_priority: list[DigestItem] = Field(default_factory=list)


class DigestCounts(CamelModel):
    urgent: int
    important: int
    low_priority: int
    needs_review: int


class DigestWindow(CamelModel):
    start_utc: str
    end_utc: str


class ModelInfo(CamelModel):
    profile: str
    provider: str | None = None
    model: str | None = None
    locality: str | None = None


class DigestResult(CamelModel):
    date: str
    timezone: str
    window: DigestWindow
    processed_count: int
    truncated: bool
    counts: DigestCounts
    groups: DigestGroups
    model: ModelInfo
