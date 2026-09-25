"""Runtime operations behind the daemon's internal API."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from typing import Any

from crewquarters_runtime import probe
from crewquarters_runtime.config import DaemonConfig
from crewquarters_runtime.docker_api import DockerClient, DockerError
from crewquarters_runtime.downloads import ModelStore
from crewquarters_runtime.specs import (
    LABEL_KIND,
    ModelProfile,
    RunSpec,
    SpecError,
    agent_container_config,
    load_profiles,
    model_container_config,
    require_immutable_image,
)

log = logging.getLogger("crewquarters.runtime")

RUN_REF = re.compile(
    r"^cq-run-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-[1-9][0-9]{0,5}$"
)
GC_AFTER_SECONDS = 600


class Engine:
    def __init__(self, cfg: DaemonConfig, docker: DockerClient | None = None) -> None:
        self.cfg = cfg
        self.docker = docker or DockerClient(cfg.docker_socket)
        self.profiles = load_profiles(cfg.model_profiles_dir)
        self.store = ModelStore(cfg.models_dir, cfg.hf_endpoint, cfg.disk_reserve_bytes)
        self._start_lock = threading.Lock()  # agent runs
        self._model_locks: dict[str, threading.Lock] = {}
        self._model_locks_guard = threading.Lock()
        cfg.runs_dir.mkdir(parents=True, exist_ok=True)

    # --- setup ------------------------------------------------------------------------

    def ensure_networks(self) -> None:
        """Agent and model networks are internal (no route to the LAN or internet) and,
        where Docker supports it (28+), isolated: the bridge has no host address, so a
        container cannot reach host services either. On older engines the .deb installs
        a firewall rule for the stable bridge names instead (``host_isolation`` reports
        which applies). The capability broker joins the agent network; the model gateway
        joins the model network."""
        self.host_isolation: dict[str, str] = {}
        for name, isolate in (
            (self.cfg.agent_network, self.cfg.isolate_agent_network),
            (self.cfg.model_network, self.cfg.isolate_model_network),
        ):
            options = {"com.docker.network.bridge.name": bridge_name(name)}
            if self.docker.network_inspect(name) is None:
                try:
                    self.docker.network_create(
                        name,
                        internal=True,
                        labels={"io.crewquarters.managed": "true"},
                        options={
                            **options,
                            "com.docker.network.bridge.gateway_mode_ipv4": "isolated",
                        }
                        if isolate
                        else options,
                    )
                except DockerError as exc:
                    if exc.status == 409:
                        pass
                    elif isolate and exc.status in (400, 500):
                        log.warning("isolated gateway mode unsupported; relying on host firewall")
                        self.docker.network_create(
                            name,
                            internal=True,
                            labels={"io.crewquarters.managed": "true"},
                            options=options,
                        )
                    else:
                        raise
            network = self.docker.network_inspect(name) or {}
            if not network.get("Internal"):
                raise SpecError(
                    "NETWORK_NOT_INTERNAL",
                    f"Docker network {name} exists but is not internal; refusing to use it.",
                )
            mode = (network.get("Options") or {}).get("com.docker.network.bridge.gateway_mode_ipv4")
            self.host_isolation[name] = "isolated" if mode == "isolated" else "firewall-required"

    def capacity(self) -> dict[str, Any]:
        result = probe.capacity(self.docker, self.cfg.data_dir)
        result["networks"] = dict(getattr(self, "host_isolation", {}))
        return result

    # --- images -------------------------------------------------------------------------

    def pull(self, image: str) -> dict[str, Any]:
        require_immutable_image(image)
        existing = self.docker.image_inspect(image)
        if existing is not None:
            return {"image": image, "status": "present", "id": existing.get("Id")}
        last = {}
        for event in self.docker.image_pull(image):
            last = event
        inspected = self.docker.image_inspect(image) or {}
        return {
            "image": image,
            "status": "pulled",
            "id": inspected.get("Id"),
            "last": last.get("status"),
        }

    def _ensure_image(self, image: str) -> None:
        if self.docker.image_inspect(image) is None:
            self.pull(image)

    # --- agent runs -------------------------------------------------------------------

    def start_run(self, body: dict[str, Any]) -> dict[str, Any]:
        spec = RunSpec.parse(body, self.cfg)
        arch = probe.architecture()
        if spec.architectures and arch not in spec.architectures:
            raise SpecError("ARCH_UNSUPPORTED", f"Agent image has no {arch} variant.")
        name = spec.container_name
        with self._start_lock:  # idempotent on (run_id, attempt)
            existing = self.docker.container_inspect(name)
            if existing is not None:
                if existing["State"]["Status"] == "created":
                    self.docker.container_start(name)
                return {"runtimeRef": name, "created": False}
            self._ensure_image(spec.image)
            run_dir = self.cfg.runs_dir / str(spec.run_id) / str(spec.attempt)
            run_dir.mkdir(parents=True, exist_ok=True)
            # The non-root agent user must be able to read its (non-secret) config.
            os.chmod(run_dir, 0o755)  # noqa: S103
            os.chmod(run_dir.parent, 0o755)  # noqa: S103
            config_path = run_dir / "config.json"
            config_path.write_text(json.dumps(spec.config))
            os.chmod(config_path, 0o644)
            self.docker.container_create(name, agent_container_config(spec, self.cfg, config_path))
            self.docker.container_start(name)
        log.info("started run container %s", name)
        return {"runtimeRef": name, "created": True}

    def _check_ref(self, ref: str) -> None:
        if not RUN_REF.match(ref):
            raise SpecError("INVALID_RUNTIME_REF", "Unknown runtime reference.")

    def run_status(self, ref: str) -> dict[str, Any] | None:
        self._check_ref(ref)
        info = self.docker.container_inspect(ref)
        if info is None:
            return None
        state = info["State"]
        status = state.get("Status")
        mapped = {
            "created": "starting",
            "running": "running",
            "restarting": "running",
            "paused": "running",
            "exited": "exited",
            "dead": "exited",
            "removing": "exited",
        }.get(status, "exited")
        # The control plane's exit watcher reads exitCode, oomKilled and memoryLimitBytes to
        # fail a run whose container died (for example AGENT_OUT_OF_MEMORY).
        memory = (info.get("HostConfig") or {}).get("Memory")
        return {
            "runtimeRef": ref,
            "state": mapped,
            "exitCode": state.get("ExitCode") if mapped == "exited" else None,
            "oomKilled": bool(state.get("OOMKilled")),
            "startedAt": state.get("StartedAt"),
            "finishedAt": state.get("FinishedAt") if mapped == "exited" else None,
            "memoryLimitBytes": memory if isinstance(memory, int) and memory > 0 else None,
        }

    def run_logs(self, ref: str, tail: int) -> dict[str, Any]:
        self._check_ref(ref)
        if self.docker.container_inspect(ref) is None:
            raise SpecError("NOT_FOUND", "Unknown runtime reference.")
        tail = max(1, min(tail, self.cfg.log_tail_limit))
        lines = self.docker.container_logs(ref, tail)
        return {
            "runtimeRef": ref,
            "lines": [{"stream": stream, "line": line[:4000]} for stream, line in lines],
            "truncated": len(lines) >= tail,
        }

    def cancel_run(self, ref: str, grace_seconds: int) -> dict[str, Any]:
        self._check_ref(ref)
        grace = max(0, min(int(grace_seconds), 120))
        if self.docker.container_inspect(ref) is None:
            return {"runtimeRef": ref, "state": "missing"}
        self.docker.container_stop(ref, grace)  # SIGTERM, then SIGKILL after the grace period
        status = self.run_status(ref) or {"state": "missing"}
        self._remove_run(ref)
        return {"runtimeRef": ref, "state": "exited", "exitCode": status.get("exitCode")}

    def _remove_run(self, ref: str) -> None:
        self.docker.container_remove(ref)
        match = re.match(r"^cq-run-(.+)-(\d+)$", ref)
        if match:
            shutil.rmtree(self.cfg.runs_dir / match.group(1) / match.group(2), ignore_errors=True)
            parent = self.cfg.runs_dir / match.group(1)
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()

    def gc(self, older_than_seconds: int = GC_AFTER_SECONDS) -> list[str]:
        """Remove exited run containers once the control plane has had time to read them."""
        removed = []
        now = time.time()
        for container in self.docker.container_list({LABEL_KIND: "run"}):
            if container.get("State") not in ("exited", "dead"):
                continue
            name = container["Names"][0].lstrip("/")
            info = self.docker.container_inspect(name)
            finished = (info or {}).get("State", {}).get("FinishedAt", "")
            ts = _parse_docker_time(finished)
            if ts is not None and now - ts > older_than_seconds:
                self._remove_run(name)
                removed.append(name)
        return removed

    # --- models -----------------------------------------------------------------------

    def profile(self, model_id: str) -> ModelProfile:
        profile = self.profiles.get(model_id)
        if profile is None:
            raise SpecError("NOT_FOUND", f"Model {model_id} is not in the allowlisted catalog.")
        return profile

    def model_state(self, model_id: str) -> dict[str, Any]:
        profile = self.profile(model_id)
        info = self.docker.container_inspect(profile.container_name)
        container: dict[str, Any] = {"state": "absent"}
        if info is not None:
            networks = info.get("NetworkSettings", {}).get("Networks", {})
            net = networks.get(self.cfg.model_network, {})
            state = info["State"]
            container = {
                "state": state.get("Status"),
                "exitCode": state.get("ExitCode"),
                "oomKilled": bool(state.get("OOMKilled")),
                "startedAt": state.get("StartedAt"),
                "endpoint": {
                    "name": profile.container_name,
                    "ip": net.get("IPAddress") or None,
                    "port": int(profile.launch.get("port", 8000)),
                    "servedModelName": profile.id,
                    "healthPath": profile.launch.get("healthPath", "/v1/models"),
                },
            }
        return {"modelId": model_id, "files": self.store.state(profile), "container": container}

    def install_model(self, model_id: str) -> dict[str, Any]:
        return self.store.start_install(self.profile(model_id))

    def cancel_install(self, model_id: str, clear: bool) -> dict[str, Any]:
        return self.store.cancel_install(self.profile(model_id), clear)

    def delete_model(self, model_id: str) -> dict[str, Any]:
        profile = self.profile(model_id)
        if self.docker.container_inspect(profile.container_name) is not None:
            raise SpecError("MODEL_RESIDENT", "Stop the model before deleting its files.")
        return self.store.delete(profile)

    def _model_lock(self, model_id: str) -> threading.Lock:
        with self._model_locks_guard:
            return self._model_locks.setdefault(model_id, threading.Lock())

    def prepare_model(self, model_id: str) -> dict[str, Any]:
        """Pull the serving image. Runs without any lock: a multi-GB vLLM pull must not
        block agent runs or other models, and ``start`` stays fast afterwards."""
        profile = self.profile(model_id)
        return self.pull(profile.launch["image"])

    def start_model(self, model_id: str) -> dict[str, Any]:
        profile = self.profile(model_id)
        files = self.store.state(profile)
        if files["state"] != "INSTALLED":
            raise SpecError("MODEL_NOT_INSTALLED", "Install the model before loading it.")
        self._ensure_image(profile.launch["image"])  # normally a no-op after prepare
        with self._model_lock(model_id):
            info = self.docker.container_inspect(profile.container_name)
            if info is not None and info["State"].get("Status") in ("exited", "dead", "created"):
                self.docker.container_remove(profile.container_name)
                info = None
            if info is None:
                config = model_container_config(profile, self.cfg, self.store.install_path(profile))
                self.docker.container_create(profile.container_name, config)
                self.docker.container_start(profile.container_name)
                log.info("started model container %s", profile.container_name)
        return self.model_state(model_id)

    def stop_model(self, model_id: str, grace_seconds: int = 30) -> dict[str, Any]:
        """Stop and remove the serving container, then verify that it is gone and sample
        memory so the gateway can confirm the release (PLAN.md section 8.3)."""
        profile = self.profile(model_id)
        before = probe.meminfo()["availableBytes"]
        gpu_before = _gpu_used()
        with self._model_lock(model_id):  # waits for an in-flight start to finish
            if self.docker.container_inspect(profile.container_name) is not None:
                self.docker.container_stop(profile.container_name, grace_seconds)
                self.docker.container_remove(profile.container_name)
            removed = self.docker.container_inspect(profile.container_name) is None
        time.sleep(0.5)
        after = probe.meminfo()["availableBytes"]
        return {
            "modelId": model_id,
            "containerRemoved": removed,
            "memoryAvailableBeforeBytes": before,
            "memoryAvailableAfterBytes": after,
            "gpuMemoryUsedBeforeBytes": gpu_before,
            "gpuMemoryUsedAfterBytes": _gpu_used(),
        }

    def reconcile(self) -> None:
        """Startup and periodic housekeeping: networks, orphaned run dirs, old containers."""
        self.ensure_networks()
        try:
            self.gc()
        except DockerError:
            log.exception("run container GC failed")


def bridge_name(network: str) -> str:
    """Stable Linux bridge name (15-character limit) for firewall rules."""
    import hashlib

    prefix = "cqa" if "agent" in network else "cqm"
    return f"{prefix}-{hashlib.sha1(network.encode()).hexdigest()[:8]}"  # noqa: S324


def _gpu_used() -> int | None:
    info = probe.gpu()
    devices = info.get("devices") or []
    used = [d.get("memoryUsedBytes") for d in devices if d.get("memoryUsedBytes") is not None]
    return sum(used) if used else None


def _parse_docker_time(value: str) -> float | None:
    if not value or value.startswith("0001-"):
        return None
    try:
        trimmed = re.sub(r"\.(\d{6})\d*", r".\1", value.replace("Z", "+00:00"))
        return datetime.fromisoformat(trimmed).timestamp()
    except ValueError:
        return None


def new_request_id() -> str:
    return uuid.uuid4().hex
