"""Typed platform configuration loaded from ``CQ_*`` environment variables.

Every variable is documented in ``docs/configuration.md`` with its type, default,
secret status, and deployment profiles (PLAN.md section 20, rule 4).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_contracts_dir() -> Path:
    # packages/shared_python/src/crewquarters_shared/config.py -> packages/contracts
    return Path(__file__).resolve().parents[3] / "contracts"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CQ_", env_file=None, extra="ignore")

    profile: str = Field("dev", description="Deployment profile: dev, demo-cpu, dgx")
    database_url: str = (
        "postgresql+psycopg://crewquarters:crewquarters@127.0.0.1:55432/crewquarters"
    )
    db_pool_size: int = 10

    secret_key: SecretStr = SecretStr("dev-insecure-secret-key-change-me-0000000000")
    capability_signing_key: SecretStr = SecretStr("dev-insecure-capability-key-change-me-00000")
    internal_service_token: SecretStr = SecretStr("dev-insecure-internal-token-change-me-0000")

    public_origins: list[str] = ["http://localhost:8080", "http://127.0.0.1:8080"]
    cookie_secure: bool = False
    session_idle_seconds: int = 12 * 3600
    session_absolute_seconds: int = 7 * 24 * 3600
    auth_rate_limit_per_minute: int = 10
    max_body_bytes: int = 2 * 1024 * 1024

    contracts_dir: Path = Field(default_factory=_default_contracts_dir)
    catalog_dir: Path | None = None

    runtime_adapter: str = Field("fake", description="fake | daemon")
    runtime_socket: Path = Path("/run/crewquarters/runtime.sock")
    model_gateway_adapter: str = "fake"
    broker_adapter: str = "fake"
    fake_connections: list[str] = ["google", "twilio", "openai", "anthropic"]

    broker_url: str = "http://capability-broker:8000"
    heartbeat_timeout_seconds: int = 30
    prepare_timeout_seconds: int = 600

    scheduler_tick_seconds: float = 1.0
    misfire_grace_seconds: int = 60
    job_lease_seconds: int = 30
    worker_concurrency: int = 4
    reconciler_interval_seconds: float = 2.0
    scheduler_metrics_host: str = "127.0.0.1"
    scheduler_metrics_port: int = 9101


@lru_cache
def get_settings() -> Settings:
    return Settings()
