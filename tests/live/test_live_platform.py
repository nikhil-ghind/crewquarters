"""Opt-in checks against a real Crewquarters platform (marker `live`).

These drive Person 1's control API (packages/contracts/openapi.yaml) the way the web UI does:
sign in, then send the session cookie, the CSRF token, and an allowed Origin on every write.
Every test is skipped unless the variables it needs are set:

    CREWQ_LIVE_PLATFORM_URL            e.g. https://gb10.local (must be one of CQ_PUBLIC_ORIGINS)
    CREWQ_LIVE_USERNAME                owner username
    CREWQ_LIVE_PASSWORD                owner password
    CREWQ_LIVE_PROBE_INSTALLATION      installation id of contract-probe (expectIsolation: true)
    CREWQ_LIVE_DIGEST_INSTALLATION     installation id of daily-gmail-digest (Google test account)
    CREWQ_LIVE_CALLER_INSTALLATION     installation id of caller (verified, consenting numbers only)
    CREWQ_LIVE_ALLOW_CALLS=1           required before the caller test approves real calls
    CREWQ_LIVE_VERIFY_TLS=0            only for a self-signed LAN certificate

Run: uv run pytest -m live tests/live
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from crewquarters_fake.contracts import load_manifest

pytestmark = pytest.mark.live
REPO = Path(__file__).resolve().parents[2]
SETTLED = {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is not set")
    return value


@pytest.fixture(scope="module")
def api() -> Iterator[httpx.Client]:
    url = env("CREWQ_LIVE_PLATFORM_URL").rstrip("/")
    credentials = {"username": env("CREWQ_LIVE_USERNAME"), "password": env("CREWQ_LIVE_PASSWORD")}
    verify = os.environ.get("CREWQ_LIVE_VERIFY_TLS") != "0"
    with httpx.Client(base_url=url, timeout=30, verify=verify, headers={"Origin": url}) as client:
        session = client.post("/api/v1/sessions", json=credentials).raise_for_status()
        client.headers["X-CSRF-Token"] = session.json()["csrfToken"]
        yield client


def start_run(api: httpx.Client, installation: str) -> str:
    response = api.post("/api/v1/runs", json={"installationId": installation})
    response.raise_for_status()
    return str(response.json()["id"])


def wait(api: httpx.Client, run_id: str, timeout: float = 900) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run: dict[str, Any] = api.get(f"/api/v1/runs/{run_id}").raise_for_status().json()
        if run["state"] in SETTLED:
            return run
        time.sleep(3)
    raise AssertionError(f"run {run_id} did not settle in {timeout}s")


def result_validator(agent_dir: str) -> Draft202012Validator:
    manifest = load_manifest(REPO / "agents" / agent_dir / "manifest.yaml")
    return Draft202012Validator(manifest["spec"]["resultSchema"])


def test_contract_probe_passes_on_the_real_platform(api: httpx.Client) -> None:
    run = wait(api, start_run(api, env("CREWQ_LIVE_PROBE_INSTALLATION")))
    assert run["state"] == "SUCCEEDED", run["error"]
    failed = [c for c in run["result"]["checks"] if c["status"] == "failed"]
    assert failed == []


def test_gmail_digest_produces_a_valid_digest(api: httpx.Client) -> None:
    run = wait(api, start_run(api, env("CREWQ_LIVE_DIGEST_INSTALLATION")))
    assert run["state"] == "SUCCEEDED", run["error"]
    result_validator("gmail_digest").validate(run["result"])


def test_caller_places_approved_calls_without_duplicates(api: httpx.Client) -> None:
    installation = env("CREWQ_LIVE_CALLER_INSTALLATION")
    if os.environ.get("CREWQ_LIVE_ALLOW_CALLS") != "1":
        pytest.skip(
            "set CREWQ_LIVE_ALLOW_CALLS=1 to place real calls to verified, consenting numbers"
        )
    run_id = start_run(api, installation)
    deadline = time.monotonic() + 300
    request: dict[str, Any] | None = None
    while request is None and time.monotonic() < deadline:
        params = {"state": "pending", "runId": run_id}
        pending = api.get("/api/v1/input-requests", params=params).raise_for_status().json()
        request = next(iter(pending["items"]), None)
        time.sleep(2)
    assert request is not None, "the caller never asked for approval"
    recipients = request["preview"]["blocks"][0]
    assert all("••••" in row[2] for row in recipients["rows"])
    api.post(
        f"/api/v1/input-requests/{request['id']}/answer",
        json={"version": request["version"], "value": {"choice": "approve"}},
    ).raise_for_status()
    run = wait(api, run_id)
    assert run["state"] == "SUCCEEDED", run["error"]
    result_validator("caller").validate(run["result"])
    sids = [r["callSid"] for r in run["result"]["rows"] if r.get("callSid")]
    assert len(sids) == len(set(sids)) == run["result"]["summary"]["called"]
