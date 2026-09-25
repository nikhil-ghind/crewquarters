"""Daemon configuration from ``CQ_RUNTIME_*`` environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class DaemonConfig:
    socket_path: Path = field(
        default_factory=lambda: Path(_env("CQ_RUNTIME_SOCKET", "/run/crewquarters/runtime.sock"))
    )
    socket_mode: int = field(default_factory=lambda: int(_env("CQ_RUNTIME_SOCKET_MODE", "0660"), 8))
    token_file: Path | None = field(
        default_factory=lambda: (
            Path(os.environ["CQ_RUNTIME_TOKEN_FILE"])
            if os.environ.get("CQ_RUNTIME_TOKEN_FILE")
            else None
        )
    )
    token: str | None = field(default_factory=lambda: os.environ.get("CQ_INTERNAL_SERVICE_TOKEN"))
    docker_socket: Path = field(
        default_factory=lambda: Path(_env("CQ_RUNTIME_DOCKER_SOCKET", "/var/run/docker.sock"))
    )
    data_dir: Path = field(
        default_factory=lambda: Path(_env("CQ_RUNTIME_DATA_DIR", "/var/lib/crewquarters"))
    )
    model_profiles_dir: Path = field(
        default_factory=lambda: Path(
            _env("CQ_RUNTIME_MODEL_PROFILES", "/usr/share/crewquarters/catalog/models")
        )
    )
    agent_network: str = field(
        default_factory=lambda: _env("CQ_RUNTIME_AGENT_NETWORK", "cq-agents")
    )
    model_network: str = field(
        default_factory=lambda: _env("CQ_RUNTIME_MODEL_NETWORK", "cq-models")
    )
    hf_endpoint: str = field(
        default_factory=lambda: _env("CQ_RUNTIME_HF_ENDPOINT", "https://huggingface.co")
    )
    # Isolated gateway mode gives the bridge no host address, so containers cannot
    # reach host services. Tests turn it off for the model network so the host-side
    # test process can call model containers directly.
    isolate_agent_network: bool = True
    isolate_model_network: bool = field(
        default_factory=lambda: _env("CQ_RUNTIME_ISOLATE_MODEL_NETWORK", "true") == "true"
    )
    agent_user: str = "65532:65532"
    max_agent_cpu: float = 8.0
    max_agent_memory_mb: int = 16384
    max_agent_pids: int = 4096
    log_tail_limit: int = 2000
    disk_reserve_bytes: int = field(
        default_factory=lambda: int(_env("CQ_RUNTIME_DISK_RESERVE_BYTES", str(20 * 1024**3)))
    )

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    def service_token(self) -> str:
        if self.token_file is not None:
            return self.token_file.read_text().strip()
        if self.token:
            return self.token
        raise RuntimeError("Set CQ_RUNTIME_TOKEN_FILE or CQ_INTERNAL_SERVICE_TOKEN.")
