"""crewctl command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from crewctl import __version__
from crewctl.build import build
from crewctl.publish import PublishError, publish
from crewctl.scaffold import ScaffoldError, scaffold
from crewctl.testing import ScenarioError, run_scenario
from crewctl.validate import capabilities_of, validate_path
from crewquarters_fake.client import FakePlatformError


@click.group()
@click.version_option(__version__, prog_name="crewctl")
def cli() -> None:
    """Scaffold, validate, test, build, and publish Crewquarters agents."""


@cli.command()
@click.argument("name")
@click.option(
    "--dir", "directory", type=click.Path(path_type=Path), help="Target directory (default: ./NAME)."
)
def init(name: str, directory: Path | None) -> None:
    """Create a new agent from the standard template."""
    target = directory or Path(name)
    try:
        written = scaffold(name, target)
    except ScaffoldError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Created {name} in {target} ({len(written)} files).")
    click.echo("Next: crewctl validate --allow-unbuilt && crewctl test")


@cli.command()
@click.argument("path", default=".", type=click.Path(path_type=Path))
@click.option("--allow-unbuilt", is_flag=True, help="Accept the REQUIRED_DIGEST placeholder image.")
@click.option("--json", "as_json", is_flag=True, help="Print a JSON report.")
def validate(path: Path, allow_unbuilt: bool, as_json: bool) -> None:
    """Validate an agent manifest (PATH is the agent directory or manifest file)."""
    manifest, issues = validate_path(path, allow_unbuilt=allow_unbuilt)
    if as_json:
        report: dict[str, Any] = {
            "valid": not issues,
            "errors": [{"path": i.path, "message": i.message} for i in issues],
        }
        if not issues and manifest is not None:
            report["capabilities"] = capabilities_of(manifest)
        click.echo(json.dumps(report))
    elif issues:
        for issue in issues:
            click.echo(f"✗ {issue.path}: {issue.message}")
    else:
        assert manifest is not None
        click.echo(f"✓ {manifest['metadata']['id']} {manifest['metadata']['version']} is valid")
        click.echo(f"  capabilities: {', '.join(capabilities_of(manifest)) or 'none'}")
    if issues:
        raise SystemExit(1)


def _summary(event: dict[str, Any]) -> str:
    payload = event["payload"]
    kind = event["type"]
    if kind == "status":
        return f"{payload.get('from')} → {payload.get('to')}"
    if kind == "log":
        return f"[{payload.get('level')}] {payload.get('message')}"
    if kind == "progress":
        return f"{payload.get('percent')}% {payload.get('message')}"
    if kind in {"input.requested", "input.answered"}:
        return str(payload.get("key"))
    if kind == "llm.call":
        return f"{payload.get('profile')} ({payload.get('locality')})"
    if kind == "connector.call":
        return f"{payload.get('operation')} {payload.get('outcome')}"
    return json.dumps(payload)


@cli.command("test")
@click.argument("path", default=".", type=click.Path(path_type=Path))
@click.option("--scenario", default="default", show_default=True)
@click.option("--docker", is_flag=True, help="Run the pinned image against the Compose fake platform.")
@click.option(
    "--platform-url", default="http://127.0.0.1:8080", show_default=True, help="Fake platform for --docker."
)
@click.option("--timeout", default=60.0, show_default=True, type=float)
@click.option("--json", "as_json", is_flag=True)
def test_command(
    path: Path, scenario: str, docker: bool, platform_url: str, timeout: float, as_json: bool
) -> None:
    """Run the agent against a fake-platform scenario."""
    try:
        outcome = run_scenario(path, scenario, docker=docker, timeout=timeout, platform_url=platform_url)
    except (ScenarioError, FakePlatformError, FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(
            json.dumps(
                {
                    "state": outcome.state,
                    "exitCode": outcome.exit_code,
                    "result": outcome.result,
                    "error": outcome.error,
                    "events": outcome.events,
                }
            )
        )
    else:
        for event in outcome.events:
            click.echo(f"{event['sequence']:>4}  {event['type']:<17} {_summary(event)}")
        click.echo(f"\nRun {outcome.state} (exit {outcome.exit_code})")
        if outcome.result is not None:
            click.echo(json.dumps(outcome.result, indent=2))
        if outcome.error:
            click.echo(f"{outcome.error.get('code')}: {outcome.error.get('message')}")
    if outcome.state != "SUCCEEDED":
        raise SystemExit(1)


@cli.command("build")
@click.argument("path", default=".", type=click.Path(path_type=Path))
@click.option("--platform", "platforms", help="Comma-separated platforms (default: both when pushing).")
@click.option("--push", is_flag=True, help="Push to the registry and pin the digest in manifest.yaml.")
@click.option("--registry", default="localhost:5001", show_default=True)
@click.option(
    "--output-manifest",
    type=click.Path(path_type=Path),
    help="Write the pinned manifest here instead of updating manifest.yaml (CI and E2E).",
)
def build_command(
    path: Path, platforms: str | None, push: bool, registry: str, output_manifest: Path | None
) -> None:
    """Build the agent image with docker buildx."""
    result = build(path, platforms=platforms, push=push, registry=registry, output_manifest=output_manifest)
    if result.pinned_image:
        click.echo(f"Pushed {result.tag} for {result.platforms}")
        click.echo(f"Pinned manifest image to {result.pinned_image}")
    else:
        click.echo(f"Built {result.tag} for {result.platforms}; manifest.yaml is not pinned (use --push).")


@cli.command("publish")
@click.argument("path", default=".", type=click.Path(path_type=Path))
@click.option(
    "--target", type=click.Choice(["local"]), required=True, help="Only the local catalog is supported."
)
@click.option("--platform-url", default="http://localhost:8080", show_default=True)
def publish_command(path: Path, target: str, platform_url: str) -> None:
    """Import the digest-pinned agent into the owner-operated local catalog."""
    try:
        entry = publish(path, platform_url)
    except PublishError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Published {entry['agentId']} {entry['version']} ({entry['imageDigest']}) to {platform_url}")


def main() -> None:
    cli()
