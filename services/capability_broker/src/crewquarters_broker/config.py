"""Broker settings: the shared ``CQ_*`` settings plus broker-only variables.

Every variable is documented in ``docs/capability-broker.md``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from crewquarters_secret_store import Keyring
from pydantic import SecretStr

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

    @property
    def google_redirect_uri(self) -> str:
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
