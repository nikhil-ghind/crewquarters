"""Model gateway settings (``CQ_GATEWAY_*`` plus the shared ``CQ_*`` settings)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
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
    wait_ready_seconds: int = 900

    max_output_tokens: int = 8192
    per_run_token_limit: int = 200_000
    daily_cloud_token_budget: int = 0  # 0 disables the daily budget
    request_timeout_seconds: float = 300.0

    anthropic_default_model: str = "claude-opus-5"
    # openai.<name> -> model id, e.g. {"default": "<model>"}. No default is assumed.
    openai_models: dict[str, str] = Field(default_factory=dict)
    anthropic_models: dict[str, str] = Field(default_factory=dict)
    anthropic_fallbacks: bool = True

    reaper_interval_seconds: float = 2.0
    job_lease_seconds: int = 30


@lru_cache
def get_gateway_settings() -> GatewaySettings:
    return GatewaySettings()
