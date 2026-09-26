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
    # The development key in infra/livekit/*.yaml (never used on a reachable network).
    livekit_api_key: str = "crewq-dev"
    livekit_api_secret: str = "crewquarters-local-development-secret-0001"  # noqa: S105
    sip_trunk_id: str | None = None  # dial real numbers through this LiveKit SIP trunk
    # A real Crewquarters model gateway (for example a GB10 appliance's, through an SSH tunnel)
    # for the model facade's chat, speech-to-text, and speech. Unset keeps the local backends.
    gateway_url: str | None = None
    gateway_service_token: str | None = None
    gateway_voice_token: str | None = None
    gateway_stt_model: str = "local.asr.r2t2"
    gateway_tts_model: str = "local.tts.voxtream"
    gateway_voice: str = "female"

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
            livekit_api_key=os.environ.get("CREWQ_FAKE_LIVEKIT_API_KEY") or cls.livekit_api_key,
            livekit_api_secret=(
                os.environ.get("CREWQ_FAKE_LIVEKIT_API_SECRET") or cls.livekit_api_secret
            ),
            sip_trunk_id=os.environ.get("CREWQ_FAKE_SIP_TRUNK_ID") or None,
            gateway_url=os.environ.get("CREWQ_FAKE_GATEWAY_URL") or None,
            gateway_service_token=os.environ.get("CREWQ_FAKE_GATEWAY_SERVICE_TOKEN") or None,
            gateway_voice_token=os.environ.get("CREWQ_FAKE_GATEWAY_VOICE_TOKEN") or None,
            gateway_stt_model=os.environ.get("CREWQ_FAKE_GATEWAY_STT_MODEL")
            or cls.gateway_stt_model,
            gateway_tts_model=os.environ.get("CREWQ_FAKE_GATEWAY_TTS_MODEL")
            or cls.gateway_tts_model,
            gateway_voice=os.environ.get("CREWQ_FAKE_GATEWAY_VOICE") or cls.gateway_voice,
        )
