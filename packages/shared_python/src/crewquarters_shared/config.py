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
    # Held only by the control API and the model gateway: authorizes chat leases/inference.
    chat_client_token: SecretStr = SecretStr("dev-insecure-chat-token-change-me-000000000")

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
    model_gateway_adapter: str = Field("http", description="http (model gateway) | fake (tests)")
    model_gateway_url: str = "http://model-gateway:8090"
    broker_adapter: str | None = Field(
        None,
        description="http (capability broker) | fake (dev profile only). "
        "Unset: fake in the dev profile, http otherwise.",
    )
    fake_connections: list[str] = ["google", "twilio", "openai", "anthropic"]

    broker_url: str = "http://capability-broker:8000"
    knowledge_url: str = "http://knowledge:8000"
    # The origin the owner's browser uses (http://localhost:8080, or the LAN HTTPS name).
    # The broker builds the Google OAuth redirect from it; the control API reports it
    # read-only in /settings (docs/adr/0009-callback-base-url.md).
    public_base_url: str = "http://localhost:8080"
    # The public HTTPS origin Twilio calls back on (the callback tunnel). Unset: Twilio
    # uses public_base_url. Twilio signs the exact URL, so the broker validates against it.
    twilio_callback_base_url: str | None = None
    # Per-document upload limit, shared with the knowledge service (CQ_MAX_UPLOAD_BYTES).
    max_upload_bytes: int = 25 * 1024 * 1024
    heartbeat_timeout_seconds: int = 30
    prepare_timeout_seconds: int = 600

    scheduler_tick_seconds: float = 1.0
    misfire_grace_seconds: int = 60
    job_lease_seconds: int = 30
    worker_concurrency: int = 4
    reconciler_interval_seconds: float = 2.0
    scheduler_metrics_host: str = "127.0.0.1"
    scheduler_metrics_port: int = 9101

    def twilio_base_url(self) -> str:
        """Origin for Twilio callback URLs and signature checks, without a trailing slash."""
        return (self.twilio_callback_base_url or self.public_base_url).rstrip("/")

    def allowed_origins(self) -> set[str]:
        """``public_origins`` plus the origin of ``public_base_url``, so the UI works at the
        address the device advertises (another port, a LAN name, or the tunnel host)."""
        base = "/".join(self.public_base_url.split("/")[:3])
        return {o.rstrip("/") for o in [*self.public_origins, base] if o}

    def effective_broker_adapter(self) -> str:
        """The connection-status adapter. The fake reports every provider connected, so it
        is refused outside the ``dev`` profile."""
        adapter = self.broker_adapter or ("fake" if self.profile == "dev" else "http")
        if adapter not in ("fake", "http"):
            raise RuntimeError(f"Unknown CQ_BROKER_ADAPTER={adapter!r}; use http or fake.")
        if adapter == "fake" and self.profile != "dev":
            raise RuntimeError(
                "CQ_BROKER_ADAPTER=fake is only allowed in the dev profile; "
                f"CQ_PROFILE={self.profile!r} must use the capability broker (http)."
            )
        return adapter


@lru_cache
def get_settings() -> Settings:
    return Settings()
