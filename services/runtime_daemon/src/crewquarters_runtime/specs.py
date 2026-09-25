"""Validation of run specs and model profiles, and the hardened container configs built
from them. The daemon never accepts raw Docker options: every field of the container
config below comes either from a fixed hardening policy or from a validated value."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crewquarters_runtime.config import DaemonConfig

# Digest-pinned references only: "name@sha256:<64 hex>".
IMMUTABLE_IMAGE = re.compile(
    r"^[a-z0-9]+([._-][a-z0-9]+)*(:[0-9]+)?(/[a-z0-9]+([._-][a-z0-9]+)*)*"
    r"(:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?@sha256:[a-f0-9]{64}$"
)
ALLOWED_ENV = frozenset(
    {"PLATFORM_BROKER_URL", "PLATFORM_RUN_TOKEN", "PLATFORM_RUN_ID", "PLATFORM_ATTEMPT"}
)
PROFILE_ID = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*){1,3}$")
CONFIG_MOUNT = "/run/crewquarters/config.json"
LABEL_KIND = "io.crewquarters.kind"
LABEL_RUN = "io.crewquarters.run-id"
LABEL_ATTEMPT = "io.crewquarters.attempt"
LABEL_MODEL = "io.crewquarters.model-id"


class SpecError(ValueError):
    """A request the daemon refuses. ``code`` is returned to the caller."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def require_immutable_image(image: str) -> str:
    if not isinstance(image, str) or not IMMUTABLE_IMAGE.match(image):
        raise SpecError("MUTABLE_IMAGE", "Images must be pinned by digest (name@sha256:...).")
    return image


@dataclass(frozen=True)
class RunSpec:
    run_id: uuid.UUID
    attempt: int
    image: str
    entrypoint: list[str]
    architectures: list[str]
    cpu: float
    memory_mb: int
    pids: int
    env: dict[str, str]
    config: dict[str, Any]

    @property
    def container_name(self) -> str:
        return f"cq-run-{self.run_id}-{self.attempt}"

    @classmethod
    def parse(cls, body: dict[str, Any], cfg: DaemonConfig) -> RunSpec:
        try:
            run_id = uuid.UUID(str(body["run_id"]))
            attempt = int(body["attempt"])
            entrypoint = body["entrypoint"]
            env = body.get("env") or {}
            spec = cls(
                run_id=run_id,
                attempt=attempt,
                image=require_immutable_image(body["image"]),
                entrypoint=list(entrypoint),
                architectures=list(body.get("architectures") or []),
                cpu=float(body["cpu"]),
                memory_mb=int(body["memory_mb"]),
                pids=int(body.get("pids", 256)),
                env={str(k): str(v) for k, v in env.items()},
                config=dict(body.get("config") or {}),
            )
        except SpecError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise SpecError("INVALID_RUN_SPEC", f"Invalid run spec: {exc}") from exc
        if attempt < 1:
            raise SpecError("INVALID_RUN_SPEC", "attempt must be >= 1")
        if not spec.entrypoint or not all(
            isinstance(a, str) and 0 < len(a) <= 256 for a in spec.entrypoint
        ):
            raise SpecError("INVALID_RUN_SPEC", "entrypoint must be 1-16 non-empty strings.")
        if len(spec.entrypoint) > 16:
            raise SpecError("INVALID_RUN_SPEC", "entrypoint must be 1-16 non-empty strings.")
        if not 0 < spec.cpu <= cfg.max_agent_cpu:
            raise SpecError("RESOURCE_LIMIT", f"cpu must be in (0, {cfg.max_agent_cpu}].")
        if not 64 <= spec.memory_mb <= cfg.max_agent_memory_mb:
            raise SpecError(
                "RESOURCE_LIMIT", f"memory_mb must be in [64, {cfg.max_agent_memory_mb}]."
            )
        if not 16 <= spec.pids <= cfg.max_agent_pids:
            raise SpecError("RESOURCE_LIMIT", f"pids must be in [16, {cfg.max_agent_pids}].")
        unexpected = set(spec.env) - ALLOWED_ENV
        if unexpected:
            raise SpecError(
                "ENV_NOT_ALLOWED", f"Environment variables not allowed: {sorted(unexpected)}"
            )
        if len(json.dumps(spec.config)) > 256 * 1024:
            raise SpecError("INVALID_RUN_SPEC", "config is larger than 256 KiB.")
        return spec


def agent_container_config(spec: RunSpec, cfg: DaemonConfig, config_path: Path) -> dict[str, Any]:
    """Hardened per-run container (PLAN.md section 11.3 and README "Agent lifecycle")."""
    memory = spec.memory_mb * 1024 * 1024
    env = [f"{k}={v}" for k, v in sorted(spec.env.items())]
    env.append(f"PLATFORM_CONFIG_FILE={CONFIG_MOUNT}")
    return {
        "Image": spec.image,
        "Entrypoint": spec.entrypoint[:1],
        "Cmd": spec.entrypoint[1:],
        "Env": env,
        "User": cfg.agent_user,
        "WorkingDir": "/tmp",  # noqa: S108 - container tmpfs, not the host /tmp
        "Labels": {
            LABEL_KIND: "run",
            LABEL_RUN: str(spec.run_id),
            LABEL_ATTEMPT: str(spec.attempt),
        },
        "HostConfig": {
            "ReadonlyRootfs": True,
            "Tmpfs": {"/tmp": "rw,noexec,nosuid,nodev,size=67108864"},  # noqa: S108
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Privileged": False,
            "PidsLimit": spec.pids,
            "NanoCpus": int(spec.cpu * 1_000_000_000),
            "Memory": memory,
            "MemorySwap": memory,
            "NetworkMode": cfg.agent_network,
            "Binds": [f"{config_path}:{CONFIG_MOUNT}:ro"],
            "IpcMode": "private",
            "Init": True,
            "RestartPolicy": {"Name": "no"},
            "LogConfig": {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "2"}},
        },
    }


@dataclass(frozen=True)
class ModelProfile:
    """A trusted model-serving profile read from the root-owned catalog directory."""

    id: str
    backend: str
    source: dict[str, Any]
    launch: dict[str, Any]
    memory: dict[str, Any]
    catalog_dir: Path
    raw: dict[str, Any]

    @property
    def container_name(self) -> str:
        return f"cq-model-{self.id.replace('.', '-')}"

    @property
    def revision(self) -> str:
        return str(self.source.get("revision") or "bundled")

    @classmethod
    def load(cls, path: Path) -> ModelProfile:
        data = json.loads(path.read_text())
        ident = str(data.get("id", ""))
        if not PROFILE_ID.match(ident) or path.stem != ident:
            raise SpecError("INVALID_PROFILE", f"{path.name}: id must match the file name.")
        launch = dict(data["launch"])
        require_immutable_image(launch["image"])
        if not isinstance(launch.get("args"), list) or not all(
            isinstance(a, str) for a in launch["args"]
        ):
            raise SpecError("INVALID_PROFILE", f"{ident}: launch.args must be strings.")
        return cls(
            id=ident,
            backend=str(data["backend"]),
            source=dict(data["source"]),
            launch=launch,
            memory=dict(data.get("memory") or {}),
            catalog_dir=path.parent,
            raw=data,
        )


def load_profiles(directory: Path) -> dict[str, ModelProfile]:
    profiles: dict[str, ModelProfile] = {}
    for path in sorted(directory.glob("*.json")):
        profile = ModelProfile.load(path)
        profiles[profile.id] = profile
    return profiles


def model_container_config(
    profile: ModelProfile, cfg: DaemonConfig, model_path: Path
) -> dict[str, Any]:
    """vLLM (or mock) server container. Only placeholder substitution of trusted values;
    callers cannot add flags, mounts, or devices."""
    port = int(profile.launch.get("port", 8000))
    mount = f"/models/{profile.id}"
    values = {"model_path": mount, "served_model_name": profile.id, "port": str(port)}
    args = [arg.format(**values) for arg in profile.launch["args"]]
    host: dict[str, Any] = {
        "Binds": [f"{model_path}:{mount}:ro"],
        "NetworkMode": cfg.model_network,
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges:true"],
        "Privileged": False,
        "RestartPolicy": {"Name": "no"},
        "LogConfig": {"Type": "json-file", "Config": {"max-size": "20m", "max-file": "2"}},
        # An init process forwards SIGTERM so servers stop promptly on unload.
        "Init": True,
    }
    if profile.launch.get("gpu"):
        host["DeviceRequests"] = [{"Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]]}]
    if profile.launch.get("ipcHost"):
        host["IpcMode"] = "host"
    if profile.launch.get("shmSizeBytes"):
        host["ShmSize"] = int(profile.launch["shmSizeBytes"])
    config: dict[str, Any] = {
        "Image": profile.launch["image"],
        "Entrypoint": args[:1],
        "Cmd": args[1:],
        "Env": [f"{k}={v}" for k, v in sorted((profile.launch.get("env") or {}).items())],
        "Labels": {LABEL_KIND: "model", LABEL_MODEL: profile.id},
        "ExposedPorts": {f"{port}/tcp": {}},
        "HostConfig": host,
    }
    if profile.launch.get("user"):
        config["User"] = str(profile.launch["user"])
    return config
