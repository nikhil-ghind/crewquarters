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
    # A local LiveKit server for voice calls; unset uses the offline call state machine.
    livekit_url: str | None = None  # what agents connect to (for example ws://livekit:7880)
    livekit_callee_url: str | None = None  # what the simulated callee connects to (default: same)
    livekit_api_url: str | None = None  # server API (default: the callee URL over http)
    livekit_api_key: str = "devkey"  # `livekit-server --dev` defaults
    livekit_api_secret: str = "secret"  # noqa: S105 - a development default, not a credential
    sip_trunk_id: str | None = None  # dial real numbers through this LiveKit SIP trunk

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
            livekit_url=os.environ.get("CREWQ_FAKE_LIVEKIT_URL") or None,
            livekit_callee_url=os.environ.get("CREWQ_FAKE_LIVEKIT_CALLEE_URL") or None,
            livekit_api_url=os.environ.get("CREWQ_FAKE_LIVEKIT_API_URL") or None,
            livekit_api_key=os.environ.get("CREWQ_FAKE_LIVEKIT_API_KEY", "devkey"),
            livekit_api_secret=os.environ.get("CREWQ_FAKE_LIVEKIT_API_SECRET", "secret"),
            sip_trunk_id=os.environ.get("CREWQ_FAKE_SIP_TRUNK_ID") or None,
        )
