from pathlib import Path

import yaml
from click.testing import CliRunner

from crewquarters_fake.cli import cli
from crewquarters_fake.client import FakePlatformClient
from crewquarters_fake.server import BackgroundServer

REPO = Path(__file__).resolve().parents[3]


def test_help_lists_commands() -> None:
    output = CliRunner().invoke(cli, ["--help"]).output
    for command in ("serve", "reset", "seed", "run"):
        assert command in output


def test_seed_and_reset_drive_the_admin_api(fake_server: BackgroundServer, tmp_path: Path) -> None:
    (tmp_path / "scenario.yaml").write_text(yaml.safe_dump({"name": "cli", "gmail": {"mailbox": []}}))
    seeded = CliRunner().invoke(cli, ["seed", str(tmp_path), "--url", fake_server.url])
    assert seeded.exit_code == 0, seeded.output
    assert '"name": "cli"' in seeded.output
    reset = CliRunner().invoke(cli, ["reset", "--url", fake_server.url])
    assert reset.exit_code == 0


def test_run_executes_an_agent_as_a_process(fake_server: BackgroundServer, tmp_path: Path) -> None:
    agent = REPO / "agents" / "gmail_digest"
    client = FakePlatformClient(fake_server.url)
    client.load_scenario(str(agent / "scenarios" / "default"))
    result = CliRunner().invoke(
        cli,
        [
            "run",
            str(agent),
            "--url",
            fake_server.url,
            "--launcher",
            "process",
            "--set",
            "timezone=Asia/Kolkata",
            "--log-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "SUCCEEDED" in result.output


def test_pending_and_answer_let_an_operator_approve_from_the_terminal(fake_server: BackgroundServer) -> None:
    import httpx

    client = FakePlatformClient(fake_server.url)
    manifest = yaml.safe_load((REPO / "tests/integration/agents/hello_agent/manifest.yaml").read_text())
    client.register_manifest(manifest)
    installation = client.install("hello-agent", "0.1.0", {})
    run = client.create_run(installation["id"])
    dispatch = client.dispatch(run["id"], fake_server.url)
    headers = {"Authorization": f"Bearer {dispatch['env']['PLATFORM_RUN_TOKEN']}"}
    sdk = f"{fake_server.url}/internal/v1/sdk"
    httpx.post(
        f"{sdk}/handshake",
        json={"protocol": "v1alpha1", "sdkVersion": "t", "agentId": "hello-agent"},
        headers=headers,
    )
    body = {
        "key": "greeting-v1",
        "title": "Say hello?",
        "prompt": "p",
        "schema": {
            "type": "object",
            "required": ["choice"],
            "properties": {"choice": {"enum": ["yes", "no"]}},
        },
        "choices": [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}],
        "timeoutSeconds": 60,
    }
    httpx.post(f"{sdk}/input-requests", json=body, headers=headers)

    pending = CliRunner().invoke(cli, ["pending", "--url", fake_server.url])
    assert pending.exit_code == 0 and "greeting-v1" in pending.output and "Yes" in pending.output
    answered = CliRunner().invoke(cli, ["answer", "--url", fake_server.url, "--choice", "yes"])
    assert answered.exit_code == 0, answered.output
    assert client.input_requests(state="answered")[0]["answer"]["data"] == {"choice": "yes"}
    none_left = CliRunner().invoke(cli, ["answer", "--url", fake_server.url, "--choice", "yes"])
    assert none_left.exit_code == 1 and "no pending" in none_left.output


def test_run_accepts_auto_answers(fake_server: BackgroundServer, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "run",
            str(REPO / "tests/integration/agents/hello_agent"),
            "--url",
            fake_server.url,
            "--launcher",
            "process",
            "--auto-answer",
            "greeting-*=yes",
            "--log-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert '"said": "hello"' in result.output
