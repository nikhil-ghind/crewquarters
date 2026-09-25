from pathlib import Path

import yaml
from click.testing import CliRunner

from crewctl.cli import cli


def test_init_creates_a_valid_agent(tmp_path: Path) -> None:
    target = tmp_path / "weather-bot"
    result = CliRunner().invoke(cli, ["init", "weather-bot", "--dir", str(target)])
    assert result.exit_code == 0, result.output
    for relative in (
        "manifest.yaml",
        "pyproject.toml",
        "Dockerfile",
        "README.md",
        "src/weather_bot/__init__.py",
        "src/weather_bot/__main__.py",
        "tests/test_agent.py",
        "scenarios/default/scenario.yaml",
        "scenarios/default/config.yaml",
    ):
        assert (target / relative).is_file(), relative
    manifest = yaml.safe_load((target / "manifest.yaml").read_text())
    assert manifest["metadata"]["id"] == "weather-bot"
    assert manifest["spec"]["entrypoint"] == ["python", "-m", "weather_bot"]
    assert manifest["spec"]["image"].endswith("@sha256:REQUIRED_DIGEST")
    dockerfile = (target / "Dockerfile").read_text()
    assert "USER 10001:10001" in dockerfile
    assert "@sha256:" in dockerfile.splitlines()[1]

    validated = CliRunner().invoke(cli, ["validate", str(target), "--allow-unbuilt"])
    assert validated.exit_code == 0, validated.output


def test_init_refuses_a_non_empty_directory(tmp_path: Path) -> None:
    target = tmp_path / "busy"
    target.mkdir()
    (target / "file.txt").write_text("x")
    result = CliRunner().invoke(cli, ["init", "busy", "--dir", str(target)])
    assert result.exit_code == 1
    assert "not empty" in result.output


def test_init_rejects_invalid_names(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["init", "9 lives!", "--dir", str(tmp_path / "x")])
    assert result.exit_code == 1
    assert "agent name" in result.output
