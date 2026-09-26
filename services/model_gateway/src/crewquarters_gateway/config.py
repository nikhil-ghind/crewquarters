"""Model gateway settings (``CQ_GATEWAY_*`` plus the shared ``CQ_*`` settings)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

GiB = 1024**3


def _repo_catalog() -> Path:
    # services/model_gateway/src/crewquarters_gateway/config.py -> catalog/models/dev
    return Path(__file__).resolve().parents[4] / "catalog" / "models" / "dev"


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CQ_GATEWAY_", extra="ignore")

    catalog_dir: Path = Field(default_factory=_repo_catalog)
    # "daemon": model servers run as containers via the runtime daemon.
    # "inprocess": deterministic in-process mock (unit tests, no Docker).
    runtime: str = "daemon"
    runtime_socket: Path = Path("/run/crewquarters/runtime.sock")
    # How to reach model containers: by container name on the model network (Compose,
    # appliance) or by container IP (a gateway running on the host, tests).
    model_addressing: str = "name"
    control_api_url: str = "http://control-api:8080"

    # Memory admission policy (PLAN.md section 3.1). Values are bytes.
    system_reserve_bytes: int = 24 * GiB
    max_serving_bytes: int = 96 * GiB
    load_safety_margin_bytes: int = 8 * GiB
    one_generative_model: bool = True
    idle_unload_seconds: int = 600
    manual_drain_seconds: int = 30
    load_poll_seconds: float = 0.5

    run_lease_ttl_seconds: int = 300
    chat_lease_ttl_seconds: int = 12 * 3600
    manual_lease_ttl_seconds: int = 3600
    # A voice call renews its leases on every use; this bounds a call the broker lost.
    voice_lease_ttl_seconds: int = 600
    # Timeout chain (docs/model-gateway.md, "Timeouts"): a request may wait up to
    # wait_ready_seconds for a cold model and then request_timeout_seconds for the reply.
    # The broker (crewquarters_broker.main.GATEWAY_TIMEOUT_SECONDS) and the SDK
    # (crewquarters.llm.LLM_TIMEOUT_SECONDS) each wait longer than the layer inside them.
    wait_ready_seconds: int = 900  # matches the dgx catalog startupTimeoutSeconds

    max_output_tokens: int = 8192
    # Whole-file transcription uploads (POST /audio/transcriptions); matches the control
    # API's CQ_MAX_UPLOAD_BYTES default.
    max_audio_bytes: int = 25 * 1024 * 1024
    per_run_token_limit: int = 200_000
    daily_cloud_token_budget: int = 0  # 0 disables the daily budget
    request_timeout_seconds: float = 300.0
    # Provider-profile connection tests (POST /provider-profiles/{id}/test).
    provider_test_timeout_seconds: float = 15.0

    # Device keyring shared with the capability broker (same variable name). Unset: cloud
    # providers are disabled.
    master_key_file: str | None = Field(
        None, validation_alias=AliasChoices("CQ_MASTER_KEY_FILE", "CQ_GATEWAY_MASTER_KEY_FILE")
    )
    credential_cache_seconds: float = 60.0
    # Idempotent /llm/chat replays (in-process; see docs/model-gateway.md).
    idempotency_ttl_seconds: float = 3600.0
    idempotency_max_entries: int = 2000

    anthropic_default_model: str = "claude-opus-5"
    # Operator overrides: <provider>.<name> -> model id, e.g. {"default": "<model>"}. The
    # provider profile's allowedModels are used otherwise (docs/model-gateway.md).
    openai_models: dict[str, str] = Field(default_factory=dict)
    anthropic_models: dict[str, str] = Field(default_factory=dict)
    anthropic_fallbacks: bool = True

    reaper_interval_seconds: float = 2.0
    job_lease_seconds: int = 30


@lru_cache
def get_gateway_settings() -> GatewaySettings:
    return GatewaySettings()
