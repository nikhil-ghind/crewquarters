from pathlib import Path

from click.testing import CliRunner

from crewctl.cli import cli


def test_crewctl_test_runs_a_scaffolded_agent_against_the_fake(tmp_path: Path) -> None:
    target = tmp_path / "echo-agent"
    assert CliRunner().invoke(cli, ["init", "echo-agent", "--dir", str(target)]).exit_code == 0
    result = CliRunner().invoke(cli, ["test", str(target), "--timeout", "30"])
    assert result.exit_code == 0, result.output
    assert "SUCCEEDED" in result.output
    assert "input.requested" in result.output
    assert '"greeting": "Hello"' in result.output


def test_crewctl_test_fails_for_a_failing_agent(tmp_path: Path) -> None:
    target = tmp_path / "broken-agent"
    assert CliRunner().invoke(cli, ["init", "broken-agent", "--dir", str(target)]).exit_code == 0
    main = target / "src" / "broken_agent" / "__main__.py"
    main.write_text(
        main.read_text().replace(
            "    await ctx.events.progress(10",
            "    raise RuntimeError('boom')\n    await ctx.events.progress(10",
        )
    )
    result = CliRunner().invoke(cli, ["test", str(target), "--timeout", "30"])
    assert result.exit_code == 1
    assert "FAILED" in result.output
    assert "boom" in result.output


def test_crewctl_test_json_output(tmp_path: Path) -> None:
    import json

    target = tmp_path / "json-agent"
    CliRunner().invoke(cli, ["init", "json-agent", "--dir", str(target)])
    result = CliRunner().invoke(cli, ["test", str(target), "--json"])
    report = json.loads(result.output)
    assert report["state"] == "SUCCEEDED"
    assert report["result"] == {"greeting": "Hello", "confirmed": True}
    assert any(e["type"] == "status" for e in report["events"])
