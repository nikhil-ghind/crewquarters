"""Every request and response exchanged while the bundled agents run matches the draft contracts."""

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from crewquarters_contracts import loader
from crewquarters_contracts.manifest import load_manifest
from crewquarters_fake.app import create_app
from crewquarters_fake.client import FakePlatformClient
from crewquarters_fake.harness import run_agent
from crewquarters_fake.launcher import ProcessLauncher
from crewquarters_fake.server import BackgroundServer
from crewquarters_fake.settings import FakeSettings

sys.path.insert(0, str(Path(__file__).parent))
from openapi_check import find_operation, request_schema, response_schema, validator

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "scenarios"


def run(
    client: FakePlatformClient, tmp: Path, agent: str, scenario: Path, config: dict[str, Any], **kw: Any
) -> str:
    agent_dir = REPO / "agents" / agent
    manifest = load_manifest(agent_dir / "manifest.yaml")
    client.load_scenario(str(scenario))
    client.register_manifest(manifest)
    if kw.pop("approve", False):
        client.add_auto_answer("confirm-calls-v1:*", {"choice": "approve"})
    installation = client.install(manifest["metadata"]["id"], manifest["metadata"]["version"], config)
    launcher = ProcessLauncher(manifest["spec"]["entrypoint"], agent_dir=agent_dir, log_dir=tmp)
    outcome = run_agent(client, launcher, installation["id"], timeout=90, **kw)
    assert outcome.state == "SUCCEEDED", outcome.log
    return str(outcome.run["id"])


@pytest.fixture(scope="module")
def traffic(tmp_path_factory: pytest.TempPathFactory) -> Iterator[list[dict[str, Any]]]:
    tmp = tmp_path_factory.mktemp("traffic")
    with BackgroundServer(create_app(FakeSettings(heartbeat_seconds=0.2, record_traffic=True))) as server:
        client = FakePlatformClient(server.url)
        probe = REPO / "agents" / "contract_probe"
        run(client, tmp, "contract_probe", probe / "scenarios" / "default", {"expectIsolation": False})
        run(
            client,
            tmp,
            "gmail_digest",
            FIXTURES / "digest-basic",
            {"timezone": "Asia/Kolkata"},
            trigger="schedule",
            scheduled_for="2026-09-24T04:30:00Z",
        )
        run(
            client,
            tmp,
            "caller",
            FIXTURES / "caller-basic",
            {"spreadsheetId": "caller-sheet", "callPollSeconds": 0.05},
            approve=True,
        )
        yield list(client.state("traffic"))


def split(traffic: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    return [t for t in traffic if t["path"].startswith(prefix)]


def check(records: list[dict[str, Any]], document: dict[str, Any], prefix: str) -> tuple[list[str], set[str]]:
    problems: list[str] = []
    seen: set[str] = set()
    for record in records:
        operation = find_operation(document, prefix, record["method"], record["path"])
        label = f"{record['method']} {record['path']} -> {record['status']}"
        if operation is None:
            problems.append(f"undocumented operation: {label}")
            continue
        seen.add(operation["operationId"])
        schema = request_schema(operation)
        if schema is not None and record["requestBody"] is not None:
            problems += [
                f"{label} request: {e.message}"
                for e in validator(document, schema).iter_errors(record["requestBody"])
            ]
        schema = response_schema(document, operation, record["status"])
        if schema is not None and record["responseBody"] is not None:
            problems += [
                f"{label} response: {e.message}"
                for e in validator(document, schema).iter_errors(record["responseBody"])
            ]
    return problems, seen


def test_broker_traffic_matches_the_broker_contract(traffic: list[dict[str, Any]]) -> None:
    problems, seen = check(split(traffic, "/internal/v1/sdk"), loader.broker_openapi(), "/internal/v1/sdk")
    assert problems == []
    assert {
        "handshake",
        "heartbeat",
        "postEvents",
        "postResult",
        "createInputRequest",
        "getInputRequest",
        "llmChat",
        "knowledgeSearch",
        "gmailListMessages",
        "gmailGetMessage",
        "sheetsGetValues",
        "sheetsUpdateValues",
        "createCall",
        "getCall",
        "idempotencyClaim",
        "idempotencyComplete",
    } <= seen


def test_control_traffic_matches_the_control_contract(traffic: list[dict[str, Any]]) -> None:
    problems, seen = check(split(traffic, "/api/v1"), loader.control_openapi(), "")
    assert problems == []
    assert {"createInstallation", "createRun", "getRun", "listRunEvents"} <= seen


def test_every_run_event_matches_the_event_schema(traffic: list[dict[str, Any]]) -> None:
    event_validator = Draft202012Validator(loader.run_event_schema())
    events = [
        item
        for record in traffic
        if record["path"].endswith("/events") and record["method"] == "GET" and record["responseBody"]
        for item in record["responseBody"]["items"]
    ]
    assert len(events) > 100
    problems = [
        f"{e['type']} #{e['sequence']}: {err.message}"
        for e in events
        for err in event_validator.iter_errors(e)
    ]
    assert problems == []
    assert {
        "status",
        "log",
        "progress",
        "input.requested",
        "llm.call",
        "connector.call",
        "capability.denied",
    } <= {e["type"] for e in events}
