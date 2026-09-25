"""The diagnostics bundle is redacted (PLAN.md section 23.6): secrets seeded into logs,
host files, configuration and run errors never reach the zip."""

from __future__ import annotations

import io
import logging
import tarfile
import zipfile
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from conftest import install_hello, wait_for_state
from crewquarters_api import diagnostics
from crewquarters_shared.config import Settings

# Every value below must be absent from the bundle.
SECRETS = {
    "session_token": "Zq8vN2mB5xK7pR3tW9yL4cD6fH1jG0sA",
    "bearer": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJydW4ifQ.c2lnbmF0dXJlc2lnbmF0dXJl",
    "openai": "sk-proj-AbCdEfGhIjKlMnOpQrStUvWx0123456789",
    "anthropic": "sk-ant-api03-ZyXwVuTsRqPoNmLkJiHgFeDcBa98765",
    "google_refresh": "1//0gQwErTyUiOpAsDfGhJkLzXcVbNm",
    "google_access": "ya29.a0AfH6SMBxyzAbc123",
    "oauth_code": "4/0AX4XfWjabcdefghijklmnop",
    "twilio_sid": "AC" + "0123456789abcdef" * 2,
    "twilio_auth": "f0e1d2c3b4a5968778695a4b3c2d1e0f",
    "email": "alice.operator@example.com",
    "phone_e164": "+14155550123",
    "phone_national": "(415) 555-0142",
    "db_password": "hunter2-db-pass",
    "cookie": "cq_session_value_abcdef",
    "master_key": "7f" * 32,
    "csrf_secret": "super-secret-csrf-hmac-key-0001",
    "signing_key": "capability-signing-key-0002-xyz",
    "service_token": "internal-service-token-0003-xyz",
}

LEAKY_LOG_LINES = [
    f"Authorization: Bearer {SECRETS['bearer']}",
    f"Cookie: cq_session={SECRETS['cookie']}; cq_csrf=abc",
    f"GET /api/v1/connections/google/callback?code={SECRETS['oauth_code']}&state=s1",
    f'{{"refreshToken": "{SECRETS["google_refresh"]}", "access": "{SECRETS["google_access"]}"}}',
    f"openai key {SECRETS['openai']} and anthropic {SECRETS['anthropic']}",
    f"twilio account {SECRETS['twilio_sid']} auth_token={SECRETS['twilio_auth']}",
    f"calling {SECRETS['phone_e164']} or {SECRETS['phone_national']} for {SECRETS['email']}",
    f"db postgresql://crewquarters:{SECRETS['db_password']}@postgres:5432/crewquarters",
    f"loaded keyring 1:{SECRETS['master_key']}",
    f"session token={SECRETS['session_token']}",
]


def _texts(bundle: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(bundle)) as z:
        return {name: z.read(name).decode() for name in z.namelist()}


def _assert_clean(files: dict[str, str]) -> None:
    for name, content in files.items():
        for label, secret in SECRETS.items():
            assert secret not in content, f"{label} leaked into {name}"
        # Partial leaks: the distinctive middle of long secrets.
        assert "hunter2" not in content
        assert "4155550123" not in content


def _host_tar() -> io.BytesIO:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, body in {
            "compose-ps.txt": "NAME STATUS\ncontrol-api Up (healthy)\n",
            "logs/capability-broker.log": "\n".join(LEAKY_LOG_LINES),
            "logs/runtime-daemon.log": f"started; token {SECRETS['service_token']}",
            "../escape.txt": "must be skipped",
        }.items():
            data = body.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buffer.seek(0)
    return buffer


@pytest.mark.usefixtures("catalog_synced")
async def test_bundle_redacts_seeded_secrets(
    owner: httpx.AsyncClient, platform: Any, settings: Settings, sessions: Any
) -> None:
    # A failed run whose error message quotes a secret: only the code may appear.
    installation = await install_hello(owner, {"fakeScenario": "fail"})
    run = (await owner.post("/api/v1/runs", json={"installationId": installation["id"]})).json()
    await wait_for_state(owner, run["id"], {"FAILED"})

    leaky = settings.model_copy(
        update={
            "database_url": settings.database_url,
            "secret_key": SecretStr(SECRETS["csrf_secret"]),
            "capability_signing_key": SecretStr(SECRETS["signing_key"]),
            "internal_service_token": SecretStr(SECRETS["service_token"]),
            "public_base_url": f"https://{SECRETS['email'].split('@')[1]}",
        }
    )
    # The URL password check: a settings value with a password in it.
    leaky_url = leaky.model_copy(
        update={"broker_url": f"http://svc:{SECRETS['db_password']}@broker:8000"}
    )
    host_files = diagnostics.read_host_tar(_host_tar())
    assert "../escape.txt" not in host_files and "escape.txt" not in host_files
    bundle = await diagnostics.build_bundle(
        diagnostics.Sources(settings=leaky_url, sessions=sessions, log_lines=LEAKY_LOG_LINES),
        host_files,
    )
    files = _texts(bundle)
    assert {
        "README.txt",
        "summary.json",
        "health.json",
        "system.json",
        "runs.json",
        "jobs.json",
        "config.json",
        "logs/control-api.log",
        "host/compose-ps.txt",
        "host/logs/capability-broker.log",
    } <= set(files)
    _assert_clean(files)
    # Still useful: the failed run is listed by id, state and error code only.
    assert run["id"] in files["runs.json"]
    assert "FAKE_FAILURE" in files["runs.json"]
    assert "Simulated failure" not in files["runs.json"]
    assert "control-api Up (healthy)" in files["host/compose-ps.txt"]
    assert "profile" in files["config.json"]


async def test_diagnostics_endpoint_is_owner_only_and_audited(
    client: httpx.AsyncClient, owner: httpx.AsyncClient
) -> None:
    logging.getLogger("crewquarters.test").warning(
        "leak attempt %s %s", SECRETS["openai"], SECRETS["email"]
    )
    response = await owner.get("/api/v1/system/diagnostics")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "crewquarters-diagnostics-" in response.headers["content-disposition"]
    files = _texts(response.content)
    _assert_clean(files)
    assert "leak attempt" in files["logs/control-api.log"]
    events = (await owner.get("/api/v1/audit-events?action=system.diagnostics_downloaded")).json()
    assert len(events["items"]) == 1

    owner.cookies.clear()
    assert (await owner.get("/api/v1/system/diagnostics")).status_code == 401


def test_masked_settings_hides_every_secret_field() -> None:
    settings = Settings(
        secret_key=SecretStr(SECRETS["csrf_secret"]),
        database_url=f"postgresql+psycopg://u:{SECRETS['db_password']}@db/x",
    )
    masked = diagnostics.masked_settings(settings)
    text_value = str(masked)
    assert SECRETS["csrf_secret"] not in text_value
    assert SECRETS["db_password"] not in text_value
    assert masked["secret_key"] == "[REDACTED]" or masked["secret_key"] == diagnostics.MASK
