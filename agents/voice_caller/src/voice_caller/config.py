"""Installation configuration for the voice call center agent."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

DEFAULT_DISCLOSURE = "an automated AI assistant"
# The disclosure must say the caller is not a person. An owner cannot configure it away.
_AUTOMATION_WORDS = re.compile(
    r"\b(automated|ai|artificial intelligence|virtual assistant|bot)\b", re.I
)
_TAB = r"(?P<tab>'(?:[^']|'')+'|[^!']+)"
_INPUT_RE = re.compile(_TAB + r"!A(?P<row>[1-9]\d*):D(?:\d+)?$")
_RESULT_RE = re.compile(_TAB + r"!A:J$")


def _unquote(tab: str) -> str:
    if len(tab) >= 2 and tab[0] == tab[-1] == "'":
        return tab[1:-1].replace("''", "'")
    return tab


class VoiceCallerConfig(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    spreadsheet_id: str = Field(min_length=1)
    input_range: str = "Contacts!A2:D"
    result_range: str = "Results!A:J"
    agent_name: str = Field("Sam", min_length=1, max_length=40)
    organization: str = Field(min_length=1, max_length=80)
    purpose: str = Field(min_length=1, max_length=300)
    talking_points: list[str] = Field(default_factory=list, max_length=10)
    questions: list[str] = Field(default_factory=list, max_length=8)
    disclosure: str = Field(DEFAULT_DISCLOSURE, min_length=1, max_length=120)
    max_calls: int = Field(5, ge=1, le=25)
    max_call_seconds: int = Field(240, ge=60, le=900)
    ring_timeout_seconds: int = Field(30, ge=10, le=60)
    voice: str = Field("af_sarah", min_length=1, max_length=40)
    model_profile: str = "local.general.small"
    stt_profile: str = "local.stt.small"
    tts_profile: str = "local.tts.small"

    @field_validator("talking_points", "questions")
    @classmethod
    def _short_items(cls, items: list[str]) -> list[str]:
        cleaned = [item.strip() for item in items if item.strip()]
        if any(len(item) > 300 for item in cleaned):
            raise ValueError("each item must be at most 300 characters")
        return cleaned

    @field_validator("disclosure")
    @classmethod
    def _discloses_automation(cls, value: str) -> str:
        if not _AUTOMATION_WORDS.search(value):
            raise ValueError(
                "the disclosure must say the caller is automated or an AI "
                "(for example 'an automated AI assistant')"
            )
        return value.strip()

    @field_validator("input_range")
    @classmethod
    def _input_range(cls, value: str) -> str:
        if not _INPUT_RE.match(value):
            raise ValueError(
                "inputRange must look like Contacts!A2:D (name, phone, consent, status)"
            )
        return value

    @field_validator("result_range")
    @classmethod
    def _result_range(cls, value: str) -> str:
        if not _RESULT_RE.match(value):
            raise ValueError("resultRange must look like Results!A:J")
        return value

    @model_validator(mode="after")
    def _separate_tabs(self) -> VoiceCallerConfig:
        if _unquote(self.input_tab) == _unquote(self.result_tab):
            raise ValueError(
                "resultRange must use a different tab than inputRange "
                "so results never overwrite contacts"
            )
        return self

    @property
    def input_tab(self) -> str:
        match = _INPUT_RE.match(self.input_range)
        assert match is not None
        return match.group("tab")

    @property
    def input_start_row(self) -> int:
        match = _INPUT_RE.match(self.input_range)
        assert match is not None
        return int(match.group("row"))

    @property
    def result_tab(self) -> str:
        match = _RESULT_RE.match(self.result_range)
        assert match is not None
        return match.group("tab")
