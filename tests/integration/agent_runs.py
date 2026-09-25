"""Shared helpers: install a bundled agent into the fake and run it as a local process."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from crewquarters_fake.client import FakePlatformClient
from crewquarters_fake.contracts import load_manifest
from crewquarters_fake.harness import RunOutcome, run_agent
from crewquarters_fake.launcher import LaunchHandle, ProcessLauncher

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "scenarios"
AGENTS = {"gmail_digest": REPO / "agents" / "gmail_digest", "caller": REPO / "agents" / "caller"}


def prepare(
    client: FakePlatformClient, agent: str, scenario: str, config: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    agent_dir = AGENTS[agent]
    manifest = load_manifest(agent_dir / "manifest.yaml")
    client.load_scenario(str(FIXTURES / scenario))
    client.register_manifest(manifest)
    installation = client.install(
        manifest["metadata"]["id"],
        manifest["metadata"]["version"],
        config,
        manifest["spec"]["permissions"],
    )
    return manifest, installation["id"]


def launch(
    client: FakePlatformClient,
    agent: str,
    manifest: dict[str, Any],
    installation_id: str | None,
    log_dir: Path,
    *,
    run_id: str | None = None,
    trigger: str = "manual",
    scheduled_for: str | None = None,
    on_launch: Any = None,
    timeout: float = 90,
) -> RunOutcome:
    launcher = ProcessLauncher(
        manifest["spec"]["entrypoint"], agent_dir=AGENTS[agent], log_dir=log_dir
    )
    return run_agent(
        client,
        launcher,
        installation_id,
        run_id=run_id,
        trigger=trigger,
        scheduled_for=scheduled_for,
        on_launch=on_launch,
        timeout=timeout,
    )


def assert_result_matches_manifest(manifest: dict[str, Any], result: Any) -> None:
    Draft202012Validator(manifest["spec"]["resultSchema"]).validate(result)


__all__ = ["FIXTURES", "LaunchHandle", "assert_result_matches_manifest", "launch", "prepare"]
