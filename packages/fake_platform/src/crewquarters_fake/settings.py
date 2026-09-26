"""Fake-platform settings, read from CREWQ_FAKE_* environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FakeSettings:
    heartbeat_seconds: float = 10.0
    scenarios_dir: Path | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    record_traffic: bool = False
    # A real speech server (crewq-speech); unset uses the in-process fake speech engine.
    speech_url: str | None = None

    @classmethod
    def from_env(cls) -> FakeSettings:
        scenarios = os.environ.get("CREWQ_FAKE_SCENARIOS_DIR")
        return cls(
            heartbeat_seconds=float(os.environ.get("CREWQ_FAKE_HEARTBEAT_SECONDS", "10")),
            scenarios_dir=Path(scenarios) if scenarios else None,
            llm_base_url=os.environ.get("CREWQ_FAKE_LLM_BASE_URL") or None,
            llm_model=os.environ.get("CREWQ_FAKE_LLM_MODEL") or None,
            llm_api_key=os.environ.get("CREWQ_FAKE_LLM_API_KEY") or None,
            record_traffic=os.environ.get("CREWQ_FAKE_RECORD_TRAFFIC", "") in {"1", "true", "yes"},
            speech_url=os.environ.get("CREWQ_FAKE_SPEECH_URL") or None,
        )
