"""contract-probe exercises the draft broker contract end to end against the fake platform."""

from pathlib import Path
from typing import Any

from crewctl.testing import run_scenario
from crewquarters_contracts.manifest import load_manifest
from crewquarters_fake.client import FakePlatformClient
from crewquarters_fake.harness import run_agent
from crewquarters_fake.launcher import LaunchHandle, ProcessLauncher

PROBE = Path(__file__).resolve().parents[2] / "agents" / "contract_probe"


def test_all_contract_checks_pass_against_the_fake(tmp_path: Path) -> None:
    outcome = run_scenario(PROBE, "default", log_dir=tmp_path, timeout=60)
    assert outcome.state == "SUCCEEDED", outcome.log
    statuses = {c["name"]: c["status"] for c in outcome.result["checks"]}
    assert statuses == {
        "handshake": "passed",
        "events": "passed",
        "input": "passed",
        "llm": "passed",
        "structured": "passed",
        "knowledge": "passed",
        "idempotency": "passed",
        "permissions": "passed",
        "isolation": "skipped",
    }
    denied = outcome.events_of("capability.denied")
    assert denied[0]["payload"] == {"capability": "google.gmail.readonly", "operation": "broker.gmail.list"}
    assert len(outcome.events_of("log")) >= 60


def install(client: FakePlatformClient, config: dict[str, Any]) -> tuple[dict[str, Any], str]:
    manifest = load_manifest(PROBE / "manifest.yaml")
    client.load_scenario(str(PROBE / "scenarios" / "default"))
    client.register_manifest(manifest)
    installation = client.install("contract-probe", manifest["metadata"]["version"], config)
    return manifest, installation["id"]


def test_cancellation_check_ends_the_run_cancelled(fake_client: FakePlatformClient, tmp_path: Path) -> None:
    manifest, installation = install(fake_client, {"checks": ["cancellation"], "expectIsolation": False})
    launcher = ProcessLauncher(manifest["spec"]["entrypoint"], agent_dir=PROBE, log_dir=tmp_path)

    def cancel_when_waiting(handle: LaunchHandle, run_id: str) -> None:
        def waiting() -> bool:
            return any(
                e["type"] == "log" and "waiting for cancellation" in e["payload"]["message"]
                for e in fake_client.events(run_id)
            )

        fake_client.wait_for(waiting, timeout=20)
        fake_client.cancel(run_id)

    outcome = run_agent(fake_client, launcher, installation, on_launch=cancel_when_waiting, timeout=30)
    assert outcome.state == "CANCELLED", outcome.log
    assert [e["payload"]["to"] for e in outcome.events_of("status")][-2:] == ["CANCELLING", "CANCELLED"]


def test_a_failing_check_fails_the_run_with_the_report(
    fake_client: FakePlatformClient, tmp_path: Path
) -> None:
    manifest, installation = install(
        fake_client,
        {"checks": ["events", "knowledge"], "expectIsolation": False, "knowledgeBaseId": "kb-missing"},
    )
    launcher = ProcessLauncher(manifest["spec"]["entrypoint"], agent_dir=PROBE, log_dir=tmp_path)
    outcome = run_agent(fake_client, launcher, installation, timeout=30)
    assert outcome.state == "FAILED"
    assert outcome.error["code"] == "CONTRACT_CHECKS_FAILED"
    checks = {c["name"]: c["status"] for c in outcome.error["details"]["checks"]}
    assert checks == {"events": "passed", "knowledge": "failed"}


def test_scheduled_run_reports_scheduled_for(fake_client: FakePlatformClient, tmp_path: Path) -> None:
    manifest, installation = install(fake_client, {"checks": ["handshake"], "expectIsolation": False})
    launcher = ProcessLauncher(manifest["spec"]["entrypoint"], agent_dir=PROBE, log_dir=tmp_path)
    outcome = run_agent(
        fake_client,
        launcher,
        installation,
        trigger="schedule",
        scheduled_for="2026-09-24T04:30:00Z",
        timeout=30,
    )
    assert outcome.state == "SUCCEEDED", outcome.log
    [check] = outcome.result["checks"]
    assert "schedule" in check["detail"] and "2026-09-24T04:30:00" in check["detail"]
