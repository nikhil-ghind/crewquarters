"""Personal Space configuration."""

from __future__ import annotations

import re
import zoneinfo
from functools import cache

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

_LEGACY_ZONES = frozenset({"EST5EDT", "CST6CDT", "MST7MDT", "PST8PDT"})


@cache
def _zones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


def validate_timezone(value: str) -> str:
    """Accept IANA names (including legacy links like Asia/Calcutta) and UTC, not abbreviations."""
    abbreviation = bool(re.fullmatch(r"[A-Z]{2,5}", value)) or value in _LEGACY_ZONES
    if value != "UTC" and (abbreviation or "/" not in value or value not in _zones()):
        raise ValueError(
            f"use an IANA timezone such as Asia/Kolkata or America/New_York, not {value!r}"
        )
    return value


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class PersonalSpaceConfig(CamelModel):
    knowledge_base_id: str = Field(min_length=1)
    intent: str | None = Field(None, max_length=500)
    ask_for_intent: bool = True
    intent_wait_seconds: int = Field(300, ge=5, le=3600)
    timezone: str = "UTC"
    model_profile: str = "local.general.small"
    top_k: int = Field(6, ge=1, le=20)
    max_queries: int = Field(12, ge=2, le=30)
    max_passages: int = Field(40, ge=5, le=80)
    # Cosine similarity from the real embedder. The broker applies no cutoff of its own, and the
    # fake platform's BM25 scores are on a different scale, so scenarios override this.
    min_relevance: float = Field(0.3, ge=0, le=1000)
    min_evidence_passages: int = Field(3, ge=1, le=20)
    max_prompt_chars: int = Field(12000, ge=2000, le=40000)

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        return validate_timezone(value)

    @field_validator("intent")
    @classmethod
    def _intent(cls, value: str | None) -> str | None:
        return (value or "").strip() or None
