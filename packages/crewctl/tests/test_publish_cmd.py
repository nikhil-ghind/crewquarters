from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

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
    assert "/spec/image" in result.output


def test_publish_only_supports_local_target(tmp_path: Path) -> None:
    target = pinned_agent(tmp_path)
    result = CliRunner().invoke(
        cli, ["publish", str(target), "--target", "public", "--platform-url", "http://x"]
    )
    assert result.exit_code == 2


def test_publish_reports_platform_errors(tmp_path: Path, server: BackgroundServer) -> None:
    target = pinned_agent(tmp_path)
    CliRunner().invoke(
        cli, ["publish", str(target), "--target", "local", "--platform-url", server.url]
    )
    manifest = yaml.safe_load((target / "manifest.yaml").read_text())
    manifest["spec"]["image"] = manifest["spec"]["image"].replace(DIGEST, "sha256:" + "f" * 64)
    (target / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    result = CliRunner().invoke(
        cli, ["publish", str(target), "--target", "local", "--platform-url", server.url]
    )
    assert result.exit_code == 1
    assert "AGENT_VERSION_IMMUTABLE" in result.output


def control_api_stand_in() -> FastAPI:
    """The control API's write protection: session cookie, CSRF header, and allowed Origin."""
    app = FastAPI()

    @app.post("/api/v1/sessions", status_code=201, response_model=None)
    async def login(body: dict[str, str], response: Response) -> object:
        if (body["username"], body["password"]) != ("owner", "s3cret"):
            return JSONResponse({"error": {"code": "INVALID_CREDENTIALS", "message": "bad"}}, 401)
        response.set_cookie("cq_session", "session-1", httponly=True)
        return {"csrfToken": "csrf-1"}

    @app.post("/api/v1/catalog/agents/import", status_code=201, response_model=None)
    async def import_agent(request: Request, body: dict[str, object]) -> object:
        origin = request.headers.get("origin", "")
        if request.cookies.get("cq_session") != "session-1":
            return JSONResponse({"error": {"code": "UNAUTHENTICATED", "message": "sign in"}}, 401)
        if request.headers.get("x-csrf-token") != "csrf-1" or not origin.startswith("http://"):
            return JSONResponse({"error": {"code": "CSRF_FAILED", "message": "csrf"}}, 403)
        manifest = body["manifest"]
        assert isinstance(manifest, dict)
        return {
            "agentId": manifest["metadata"]["id"],
            "latest": {"version": "0.1.0", "imageDigest": DIGEST},
        }

    return app


def test_publish_signs_in_to_the_control_api(tmp_path: Path) -> None:
    target = pinned_agent(tmp_path)
    with BackgroundServer(control_api_stand_in()) as api:
        args = ["publish", str(target), "--target", "local", "--platform-url", api.url]
        anonymous = CliRunner().invoke(cli, args)
        assert anonymous.exit_code == 1
        assert "UNAUTHENTICATED" in anonymous.output
        wrong = CliRunner().invoke(cli, [*args, "--username", "owner", "--password", "nope"])
        assert "INVALID_CREDENTIALS" in wrong.output
        signed_in = CliRunner().invoke(
            cli, [*args, "--username", "owner"], env={"CREWQ_PASSWORD": "s3cret"}
        )
        assert signed_in.exit_code == 0, signed_in.output
        assert "Published pub-agent 0.1.0" in signed_in.output


def test_publish_calls_exist_in_the_control_api_contract() -> None:
    """The stand-in above must not drift from the real control API."""
    from crewquarters_fake.contracts import control_openapi

    document = control_openapi()
    paths = document["paths"]
    assert "post" in paths["/api/v1/sessions"]
    assert "post" in paths["/api/v1/catalog/agents/import"]
    schemas = document["components"]["schemas"]
    assert set(schemas["LoginIn"]["required"]) == {"username", "password"}
    assert "csrfToken" in schemas["SessionOut"]["required"]
