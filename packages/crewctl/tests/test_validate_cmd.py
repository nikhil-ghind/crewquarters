import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from crewctl.cli import cli


def scaffold(tmp_path: Path) -> Path:
    target = tmp_path / "demo-agent"
    assert CliRunner().invoke(cli, ["init", "demo-agent", "--dir", str(target)]).exit_code == 0
    return target


def test_unbuilt_manifest_fails_without_allow_unbuilt(tmp_path: Path) -> None:
    target = scaffold(tmp_path)
    result = CliRunner().invoke(cli, ["validate", str(target)])
    assert result.exit_code == 1
    assert "/spec/image" in result.output


def test_json_output_shape(tmp_path: Path) -> None:
    target = scaffold(tmp_path)
    result = CliRunner().invoke(cli, ["validate", str(target), "--json"])
    report = json.loads(result.output)
    assert report["valid"] is False
    assert report["errors"][0]["path"] == "/spec/image"
    ok = CliRunner().invoke(
        cli, ["validate", str(target / "manifest.yaml"), "--json", "--allow-unbuilt"]
    )
    assert json.loads(ok.output) == {
        "valid": True,
        "errors": [],
        "capabilities": ["events.write", "idempotency", "user_input"],
    }


def test_schema_errors_are_reported(tmp_path: Path) -> None:
    target = scaffold(tmp_path)
    manifest = yaml.safe_load((target / "manifest.yaml").read_text())
    manifest["spec"]["privileged"] = True
    (target / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    result = CliRunner().invoke(cli, ["validate", str(target), "--allow-unbuilt"])
    assert result.exit_code == 1
    assert "privileged" in result.output


def test_missing_entrypoint_module_is_reported(tmp_path: Path) -> None:
    target = scaffold(tmp_path)
    (target / "src" / "demo_agent" / "__main__.py").unlink()
    result = CliRunner().invoke(cli, ["validate", str(target), "--allow-unbuilt", "--json"])
    report = json.loads(result.output)
    assert report["valid"] is False
    assert report["errors"] == [
        {
            "path": "/spec/entrypoint",
            "message": "module demo_agent has no src/demo_agent/__main__.py",
        }
    ]


def test_missing_manifest_is_a_clear_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["validate", str(tmp_path)])
    assert result.exit_code == 1
    assert "manifest.yaml" in result.output
