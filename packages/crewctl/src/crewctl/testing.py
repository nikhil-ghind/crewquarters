"""Run an agent scenario against the fake platform (used by `crewctl test` and agent tests)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import yaml

from crewctl.build import find_repo_root
from crewquarters_contracts.manifest import load_manifest
from crewquarters_fake.app import create_app
from crewquarters_fake.client import FakePlatformClient
from crewquarters_fake.harness import Launcher, RunOutcome, run_agent
from crewquarters_fake.launcher import DockerLauncher, ProcessLauncher
from crewquarters_fake.server import BackgroundServer
from crewquarters_fake.settings import FakeSettings
from crewquarters_fake.timeutil import iso, utcnow


class ScenarioError(Exception):
    pass


def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None


def run_scenario(
    agent_dir: Path,
    scenario: str = "default",
    *,
    docker: bool = False,
    timeout: float = 60.0,
    log_dir: Path | None = None,
    platform_url: str = "http://127.0.0.1:8080",
) -> RunOutcome:
    agent_dir = Path(agent_dir).resolve()
    manifest = load_manifest(agent_dir / "manifest.yaml")
    scenario_dir = agent_dir / "scenarios" / scenario
    doc = _read_yaml(scenario_dir / "scenario.yaml")
    if not isinstance(doc, dict):
        raise ScenarioError(f"scenario {scenario!r} not found at {scenario_dir}")
    config = _read_yaml(scenario_dir / "config.yaml") or {}
    run_spec = doc.get("run", {}) or {}
    log_dir = log_dir or Path(tempfile.mkdtemp(prefix="crewctl-"))

    launcher: Launcher
    if docker:
        client = FakePlatformClient(platform_url)
        client.reset()
        client.load_scenario(scenario_dir.relative_to(find_repo_root(agent_dir)).as_posix())
        client.import_manifest(manifest)
        launcher = DockerLauncher(log_dir=log_dir)
        return _install_and_run(client, launcher, manifest, config, run_spec, timeout)
    with BackgroundServer(create_app(FakeSettings(heartbeat_seconds=1.0))) as server:
        client = FakePlatformClient(server.url)
        client.load_scenario(str(scenario_dir))
        client.register_manifest(manifest)
        launcher = ProcessLauncher(manifest["spec"]["entrypoint"], agent_dir=agent_dir, log_dir=log_dir)
        return _install_and_run(client, launcher, manifest, config, run_spec, timeout)


def _install_and_run(
    client: FakePlatformClient,
    launcher: Launcher,
    manifest: dict[str, Any],
    config: dict[str, Any],
    run_spec: dict[str, Any],
    timeout: float,
) -> RunOutcome:
    installation = client.install(manifest["metadata"]["id"], manifest["metadata"]["version"], config)
    trigger = str(run_spec.get("trigger", "manual"))
    scheduled_for = run_spec.get("scheduledFor")
    if scheduled_for == "now":
        scheduled_for = iso(utcnow())
    return run_agent(
        client, launcher, installation["id"], trigger=trigger, scheduled_for=scheduled_for, timeout=timeout
    )
