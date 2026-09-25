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
