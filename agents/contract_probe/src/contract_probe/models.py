"""Configuration and result models for the contract probe."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

CheckName = Literal[
    "handshake",
    "events",
    "input",
    "llm",
    "structured",
    "knowledge",
    "idempotency",
    "permissions",
    "isolation",
    "cancellation",
]
Status = Literal["passed", "failed", "skipped"]
DEFAULT_CHECKS: list[CheckName] = [
    "handshake",
    "events",
    "input",
    "llm",
    "structured",
    "knowledge",
    "idempotency",
    "permissions",
    "isolation",
]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ProbeConfig(CamelModel):
    checks: list[CheckName] = Field(default_factory=lambda: list(DEFAULT_CHECKS))
    knowledge_base_id: str | None = None
    expect_isolation: bool = True
    cancel_wait_seconds: int = Field(60, ge=1, le=600)


class CheckResult(CamelModel):
    name: str
    status: Status
    detail: str
    duration_ms: int


class ProbeReport(CamelModel):
    checks: list[CheckResult]


class ProbeAnswer(BaseModel):
    answer: str
