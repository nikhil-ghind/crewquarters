from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from crewctl.cli import cli
from crewquarters_fake.app import create_app
from crewquarters_fake.client import FakePlatformClient
from crewquarters_fake.server import BackgroundServer
from crewquarters_fake.settings import FakeSettings

DIGEST = "sha256:" + "e" * 64


@pytest.fixture
def server() -> Iterator[BackgroundServer]:
    with BackgroundServer(create_app(FakeSettings())) as running:
        yield running


def pinned_agent(tmp_path: Path) -> Path:
    target = tmp_path / "pub-agent"
    CliRunner().invoke(cli, ["init", "pub-agent", "--dir", str(target)])
    manifest_path = target / "manifest.yaml"
    manifest_path.write_text(manifest_path.read_text().replace("sha256:REQUIRED_DIGEST", DIGEST))
    return target


def test_publish_imports_into_the_local_catalog(tmp_path: Path, server: BackgroundServer) -> None:
    target = pinned_agent(tmp_path)
    result = CliRunner().invoke(
        cli, ["publish", str(target), "--target", "local", "--platform-url", server.url]
    )
    assert result.exit_code == 0, result.output
    assert DIGEST in result.output
    catalog = FakePlatformClient(server.url)._call("GET", "/api/v1/catalog/agents")
    assert [e["agentId"] for e in catalog["items"]] == ["pub-agent"]


def test_publish_refuses_unbuilt_manifests(tmp_path: Path, server: BackgroundServer) -> None:
    target = tmp_path / "raw-agent"
    CliRunner().invoke(cli, ["init", "raw-agent", "--dir", str(target)])
    result = CliRunner().invoke(
        cli, ["publish", str(target), "--target", "local", "--platform-url", server.url]
    )
    assert result.exit_code == 1
    assert "spec.image" in result.output


def test_publish_only_supports_local_target(tmp_path: Path) -> None:
    target = pinned_agent(tmp_path)
    result = CliRunner().invoke(
        cli, ["publish", str(target), "--target", "public", "--platform-url", "http://x"]
    )
    assert result.exit_code == 2


def test_publish_reports_platform_errors(tmp_path: Path, server: BackgroundServer) -> None:
    target = pinned_agent(tmp_path)
    CliRunner().invoke(cli, ["publish", str(target), "--target", "local", "--platform-url", server.url])
    manifest = yaml.safe_load((target / "manifest.yaml").read_text())
    manifest["spec"]["image"] = manifest["spec"]["image"].replace(DIGEST, "sha256:" + "f" * 64)
    (target / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    result = CliRunner().invoke(
        cli, ["publish", str(target), "--target", "local", "--platform-url", server.url]
    )
    assert result.exit_code == 1
    assert "VERSION_CONFLICT" in result.output
