"""Digest configuration (spec section 8.2)."""

from __future__ import annotations

import re
import zoneinfo
from datetime import date
from functools import cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

Category = Literal["CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_UPDATES", "CATEGORY_FORUMS"]
_LEGACY_ZONES = frozenset({"EST5EDT", "CST6CDT", "MST7MDT", "PST8PDT"})


@cache
def _zones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


def validate_timezone(value: str) -> str:
    """Accept IANA names (including legacy links such as Asia/Calcutta) and UTC; reject abbreviations."""
    abbreviation = bool(re.fullmatch(r"[A-Z]{2,5}", value)) or value in _LEGACY_ZONES
    if value != "UTC" and (abbreviation or "/" not in value or value not in _zones()):
        raise ValueError(f"use an IANA timezone such as Asia/Kolkata or America/New_York, not {value!r}")
    return value


def _default_excluded() -> list[Category]:
    return ["CATEGORY_PROMOTIONS"]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class DigestConfig(CamelModel):
    timezone: str
    max_messages: int = Field(200, ge=1, le=500)
    include_labels: list[str] = Field(default_factory=list)
    exclude_categories: list[Category] = Field(default_factory=lambda: _default_excluded())
    model_profile: str = "local.general.small"
    batch_size: int = Field(10, ge=1, le=25)
    max_chars_per_message: int = Field(4000, ge=200, le=20000)
    target_date: date | None = None

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        return validate_timezone(value)
