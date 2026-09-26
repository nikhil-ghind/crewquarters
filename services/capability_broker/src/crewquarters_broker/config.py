"""Broker settings: the shared ``CQ_*`` settings plus broker-only variables.

Every variable is documented in ``docs/capability-broker.md``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr

from crewquarters_secret_store import Keyring
from crewquarters_shared.config import Settings

# Insecure development keyring so the ``dev`` profile starts without an installer.
_DEV_KEY = bytes.fromhex("00" * 31 + "01")


class BrokerSettings(Settings):
    master_key_file: Path | None = None
    provider_mode: Literal["fake", "live"] = "fake"
    public_base_url: str = "http://localhost:8080"
    control_api_url: str = "http://control-api:8080"
    knowledge_url: str = "http://knowledge:8000"
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    twilio_allowed_numbers: list[str] = []
    # Realtime voice calls (crewquarters_broker.voice): the local models they use and limits.
    voice_asr_model: str = "local.asr.r2t2"
    voice_llm_profile: str = "local.general.small"
    voice_tts_model: str = "local.tts.voxtream"
    voice_max_call_seconds: int = 300
    voice_barge_in_ms: int = 300
    # Longer than a cold load of every model the call uses.
    voice_gateway_timeout_seconds: float = 1260.0

    @property
    def google_redirect_uri(self) -> str:
        """Google redirects the owner's browser, so this uses the browser's origin
        (``CQ_PUBLIC_BASE_URL``), never the Twilio tunnel (``twilio_base_url()``)."""
        return f"{self.public_base_url.rstrip('/')}/api/v1/connections/google/callback"

    def keyring(self) -> Keyring:
        if self.master_key_file is not None:
            return Keyring.from_file(self.master_key_file)
        if self.profile != "dev":
            raise RuntimeError("CQ_MASTER_KEY_FILE is required outside the dev profile.")
        return Keyring({1: _DEV_KEY})


@lru_cache
def get_settings() -> BrokerSettings:
    return BrokerSettings()
