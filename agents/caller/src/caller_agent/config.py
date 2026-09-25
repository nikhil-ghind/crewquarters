"""Caller configuration (spec section 8.3)."""

from __future__ import annotations

import re
import zoneinfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

DEFAULT_SCRIPT = (
    "Hello {name}. This is an automated demo call from the Crewquarters team. "
    "Please say a short reply after the tone."
)
DEFAULT_DISCLOSURE = "This is an automated demonstration call. Your spoken reply will be transcribed."
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
_TAB = r"(?P<tab>'(?:[^']|'')+'|[^!']+)"
_INPUT_RE = re.compile(_TAB + r"!A(?P<row>[1-9]\d*):D(?:\d+)?$")
_RESULT_RE = re.compile(_TAB + r"!A:H$")


def _unquote(tab: str) -> str:
    if len(tab) >= 2 and tab[0] == tab[-1] == "'":
        return tab[1:-1].replace("''", "'")
    return tab


class CallerConfig(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    spreadsheet_id: str = Field(min_length=1)
    input_range: str = "Contacts!A2:D"
    result_range: str = "Results!A:H"
    script: str = Field(DEFAULT_SCRIPT, min_length=1, max_length=1000)
    disclosure: str = Field(DEFAULT_DISCLOSURE, min_length=1, max_length=500)
    max_calls: int = Field(3, ge=1, le=10)
    response_seconds: int = Field(20, ge=5, le=60)
    call_timeout_seconds: int = Field(180, ge=10, le=900)
    call_poll_seconds: float = Field(2.0, ge=0.05, le=10)
    timezone: str = "Asia/Kolkata"

    @field_validator("script")
    @classmethod
    def _only_name_placeholder(cls, value: str) -> str:
        unknown = [p for p in _PLACEHOLDER_RE.findall(value) if p != "name"]
        if unknown:
            raise ValueError(f"the script may only use the {{name}} placeholder, found {unknown}")
        return value

    @field_validator("input_range")
    @classmethod
    def _input_range(cls, value: str) -> str:
        if not _INPUT_RE.match(value):
            raise ValueError("inputRange must look like Contacts!A2:D (columns A-D starting at a row)")
        return value

    @field_validator("result_range")
    @classmethod
    def _result_range(cls, value: str) -> str:
        if not _RESULT_RE.match(value):
            raise ValueError("resultRange must look like Results!A:H")
        return value

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        if value != "UTC" and (
            re.fullmatch(r"[A-Z]{2,5}", value) or value not in zoneinfo.available_timezones()
        ):
            raise ValueError(f"use an IANA timezone such as Asia/Kolkata, not {value!r}")
        return value

    @model_validator(mode="after")
    def _separate_tabs(self) -> CallerConfig:
        if _unquote(self.input_tab) == _unquote(self.result_tab):
            raise ValueError(
                "resultRange must use a different tab than inputRange so results never overwrite contacts"
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
