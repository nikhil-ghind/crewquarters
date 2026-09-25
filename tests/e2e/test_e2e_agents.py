"""Bundled agents run from registry images, pinned by digest, under runtime hardening (spec section 10)."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from crewquarters_contracts.manifest import load_manifest
from crewquarters_fake.client import FakePlatformClient
from crewquarters_fake.harness import RunOutcome, run_agent
from crewquarters_fake.launcher import DockerLauncher

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]
PINNED = REPO / ".e2e" / "manifests"


def run_pinned(
    platform: FakePlatformClient, tmp_path: Path, agent: str, scenario: str, config: dict[str, Any], **kw: Any
) -> RunOutcome:
    manifest = load_manifest(PINNED / f"{agent}.yaml")
    platform.load_scenario(scenario)
    platform.import_manifest(manifest)
    if kw.pop("approve", False):
        platform.add_auto_answer("confirm-calls-v1:*", {"choice": "approve"})
    installation = platform.install(manifest["metadata"]["id"], manifest["metadata"]["version"], config)
    return run_agent(platform, DockerLauncher(log_dir=tmp_path), installation["id"], timeout=240, **kw)


def test_contract_probe_passes_every_check_in_the_hardened_container(
    platform: FakePlatformClient, tmp_path: Path
) -> None:
    outcome = run_pinned(
        platform,
        tmp_path,
        "contract_probe",
        "agents/contract_probe/scenarios/default",
        {"expectIsolation": True, "knowledgeBaseId": "kb-probe"},
    )
    assert outcome.state == "SUCCEEDED", outcome.log
    checks = {c["name"]: c for c in outcome.result["checks"]}
    assert {name: c["status"] for name, c in checks.items()} == {
        "handshake": "passed",
        "events": "passed",
        "input": "passed",
        "llm": "passed",
        "structured": "passed",
        "knowledge": "passed",
        "idempotency": "passed",
        "permissions": "passed",
        "isolation": "passed",
    }
    assert "uid 10001" in checks["isolation"]["detail"]


def test_gmail_digest_runs_from_its_image(platform: FakePlatformClient, tmp_path: Path) -> None:
    outcome = run_pinned(
        platform,
        tmp_path,
        "gmail_digest",
        "tests/fixtures/scenarios/digest-basic",
        {"timezone": "Asia/Kolkata"},
        trigger="schedule",
        scheduled_for="2026-09-24T04:30:00Z",
    )
    assert outcome.state == "SUCCEEDED", outcome.log
    assert [i["messageId"] for i in outcome.result["groups"]["urgent"]] == ["b-urgent-2", "b-urgent-1"]


def test_caller_runs_from_its_image_without_duplicate_calls(
    platform: FakePlatformClient, tmp_path: Path
) -> None:
    outcome = run_pinned(
        platform,
        tmp_path,
        "caller",
        "tests/fixtures/scenarios/caller-basic",
        {"spreadsheetId": "caller-sheet", "callPollSeconds": 0.2},
        approve=True,
    )
    assert outcome.state == "SUCCEEDED", outcome.log
    assert platform.state("calls")["byNumber"] == {"+15555550101": 1, "+15555550103": 1, "+15555550105": 1}


@pytest.mark.parametrize("agent", ["contract_probe", "gmail_digest", "caller"])
def test_images_run_as_non_root_and_self_check(stack: str, agent: str) -> None:
    image = load_manifest(PINNED / f"{agent}.yaml")["spec"]["image"]
    inspected = json.loads(
        subprocess.run(["docker", "image", "inspect", image], capture_output=True, check=True).stdout
    )
    assert inspected[0]["Config"]["User"] == "10001:10001"
    check = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", image, "--self-check"], capture_output=True, check=True
    )
    assert json.loads(check.stdout)["protocol"] == "v1alpha1"
