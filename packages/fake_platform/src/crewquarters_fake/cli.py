"""crewq-fake: serve the fake platform, seed or reset it, and run an agent against it."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import click
import uvicorn
import yaml

from crewquarters_fake.app import create_app
from crewquarters_fake.client import FakePlatformClient, FakePlatformError
from crewquarters_fake.contracts import load_manifest
from crewquarters_fake.harness import Launcher, run_agent
from crewquarters_fake.launcher import DockerLauncher, ProcessLauncher
from crewquarters_fake.timeutil import iso, utcnow

# The laptop Compose stack publishes the fake on 8090; 8080 is the real control API.
DEFAULT_URL = "http://127.0.0.1:8090"


@click.group()
def cli() -> None:
    """Crewquarters fake platform (development and tests only)."""


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8090, show_default=True, type=int)
def serve(host: str, port: int) -> None:
    """Serve the fake platform (settings come from CREWQ_FAKE_* environment variables)."""
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


@cli.command()
@click.option("--url", default=DEFAULT_URL, show_default=True)
def reset(url: str) -> None:
    """Clear runs, catalog, providers, faults, and auto-answers."""
    FakePlatformClient(url).reset()
    click.echo("reset")


@cli.command()
@click.argument("scenario")
@click.option("--url", default=DEFAULT_URL, show_default=True)
def seed(scenario: str, url: str) -> None:
    """Load a scenario directory (a path the fake platform can read)."""
    try:
        summary = FakePlatformClient(url).load_scenario(scenario)
    except FakePlatformError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(summary, indent=2))


def _config(config_file: Path | None, pairs: tuple[str, ...]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if config_file is not None:
        config.update(yaml.safe_load(config_file.read_text(encoding="utf-8")) or {})
    for pair in pairs:
        key, _, value = pair.partition("=")
        config[key] = yaml.safe_load(value)
    return config


@cli.command()
@click.argument("agent_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--url", default=DEFAULT_URL, show_default=True)
@click.option(
    "--launcher",
    "launcher_kind",
    type=click.Choice(["docker", "process"]),
    default="docker",
    show_default=True,
)
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(path_type=Path),
    help="Pinned manifest (docker mode).",
)
@click.option(
    "--config", "config_file", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option("--set", "pairs", multiple=True, help="Config override key=value (YAML value).")
@click.option(
    "--trigger", type=click.Choice(["manual", "schedule"]), default="manual", show_default=True
)
@click.option("--scheduled-for", help="ISO timestamp or 'now' for schedule runs.")
@click.option(
    "--auto-answer",
    "auto_answers",
    multiple=True,
    help="PATTERN=CHOICE, e.g. 'confirm-calls-v1:*=approve'.",
)
@click.option("--timeout", default=300.0, show_default=True, type=float)
@click.option("--log-dir", type=click.Path(path_type=Path))
def run(
    agent_dir: Path,
    url: str,
    launcher_kind: str,
    manifest_path: Path | None,
    config_file: Path | None,
    pairs: tuple[str, ...],
    trigger: str,
    scheduled_for: str | None,
    auto_answers: tuple[str, ...],
    timeout: float,
    log_dir: Path | None,
) -> None:
    """Install an agent in the fake platform and run it once."""
    client = FakePlatformClient(url)
    for rule in auto_answers:
        pattern, _, choice = rule.rpartition("=")
        client.add_auto_answer(pattern, {"choice": choice})
    manifest = load_manifest(manifest_path or agent_dir / "manifest.yaml")
    logs = log_dir or Path(tempfile.mkdtemp(prefix="crewq-run-"))
    launcher: Launcher
    try:
        if launcher_kind == "docker":
            client.import_manifest(manifest)
            launcher = DockerLauncher(log_dir=logs)
        else:
            client.register_manifest(manifest)
            launcher = ProcessLauncher(
                manifest["spec"]["entrypoint"], agent_dir=agent_dir, log_dir=logs
            )
        # Running an agent here means the developer approves exactly what it requests.
        installation = client.install(
            manifest["metadata"]["id"],
            manifest["metadata"]["version"],
            _config(config_file, pairs),
            manifest["spec"]["permissions"],
        )
        when = iso(utcnow()) if scheduled_for == "now" else scheduled_for
        outcome = run_agent(
            client,
            launcher,
            installation["id"],
            trigger=trigger,
            scheduled_for=when,
            timeout=timeout,
        )
    except FakePlatformError as exc:
        raise click.ClickException(str(exc)) from exc
    summary = f"Run {outcome.run['id']} {outcome.state} (exit {outcome.exit_code})"
    click.echo(f"{summary}; logs in {logs}")
    click.echo(
        json.dumps(outcome.result if outcome.result is not None else outcome.error, indent=2)
    )
    if outcome.state != "SUCCEEDED":
        raise SystemExit(1)


@cli.command()
@click.option("--url", default=DEFAULT_URL, show_default=True)
def pending(url: str) -> None:
    """List pending operator input requests (Crew Requests)."""
    requests = FakePlatformClient(url).input_requests(state="pending")
    if not requests:
        click.echo("no pending input requests")
    for request in requests:
        preview = request.get("preview") or {}
        click.echo(f"{request['id']}  {request['key']}\n  {request['title']}: {request['prompt']}")
        for block in preview.get("blocks", []):
            click.echo(f"  preview: {json.dumps(block, ensure_ascii=False)}")
        if preview.get("consequence"):
            click.echo(f"  consequence: {preview['consequence']}")
        choices = preview.get("choices", [])
        if choices:
            click.echo("  choices: " + ", ".join(f"{c['value']} ({c['label']})" for c in choices))
        else:
            click.echo(f"  answer schema: {json.dumps(request['schema'])}")


@cli.command()
@click.argument("request_id", required=False)
@click.option("--url", default=DEFAULT_URL, show_default=True)
@click.option("--choice", required=True, help="Value of the choice to submit.")
def answer(request_id: str | None, url: str, choice: str) -> None:
    """Answer a pending input request (the only pending one when REQUEST_ID is omitted)."""
    client = FakePlatformClient(url)
    requests = client.input_requests(state="pending")
    if request_id is not None:
        requests = [r for r in requests if r["id"] == request_id]
    if not requests:
        raise click.ClickException("no pending input request to answer")
    if len(requests) > 1:
        raise click.ClickException(
            "several requests are pending; pass REQUEST_ID (see `crewq-fake pending`)"
        )
    try:
        answered = client.answer(requests[0]["id"], requests[0]["version"], {"choice": choice})
    except FakePlatformError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"answered {answered['key']} with {choice}")


def main() -> None:
    cli()
