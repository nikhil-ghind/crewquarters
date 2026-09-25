"""Caller result models (camelCase on the wire)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class RowResult(CamelModel):
    row: int
    name: str
    phone_masked: str
    consent: Literal["validated", "skipped"]
    skip_reason: str | None = None
    call_status: str | None = None
    call_sid: str | None = None
    transcript: str | None = None
    sheet_write: Literal["written", "pending_retry", "failed"] | None = None
    completed_at: str | None = None
    error: str | None = None


class Summary(CamelModel):
    called: int
    answered: int
    responses_captured: int
    skipped: int
    failed: int


class CallerResult(CamelModel):
    operator_decision: Literal["approved", "cancelled", "not_required"]
    summary: Summary
    rows: list[RowResult]
