"""Cloud credentials from the secret store, provider-profile models and budgets, and the
provider-profile connection test route. Providers are always mocked."""

from __future__ import annotations

import json
import logging
import os
import stat
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from gateway_helpers import mock_cloud, run_token
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_gateway import credentials
from crewquarters_gateway.credentials import (
    CloudProfile,
    NoCredentials,
    SecretStoreCredentials,
    StaticCredentials,
)
from crewquarters_gateway.main import Gateway
from crewquarters_secret_store import Keyring
from crewquarters_secret_store import db as secret_db
from crewquarters_secret_store.db import ProviderProfile
from crewquarters_shared.db.models import AuditEvent, User
from crewquarters_shared.db.models_gateway import LlmUsage

RING = Keyring({1: bytes(range(32))})
OPENAI_KEY = "sk-openai-live-0123456789abcdef"
ANTHROPIC_KEY = "sk-ant-live-0123456789abcdef"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _user(db: AsyncSession) -> uuid.UUID:
    user = User(
        id=uuid.uuid4(),
        username="owner",
        username_normalized=f"owner-{uuid.uuid4().hex[:8]}",
        password_hash="x",
        role="owner",
    )
    db.add(user)
    await db.flush()
    return user.id


async def add_profile(
    sessions: async_sessionmaker[AsyncSession],
    provider: str,
    key: str,
    *,
    status: str = "UNTESTED",
    enabled: bool = True,
    allowed_models: list[str] | None = None,
    budgets: dict[str, Any] | None = None,
    name: str | None = None,
) -> uuid.UUID:
    """What the capability broker does when the owner saves a key."""
    async with sessions() as db:
        owner = await _user(db)
        profile = ProviderProfile(
            id=uuid.uuid4(),
            owner_id=owner,
            provider=provider,
            display_name=name or f"{provider}-{uuid.uuid4().hex[:6]}",
            allowed_models=allowed_models or [],
            budgets=budgets or {},
            enabled=enabled,
            status=status,
            settings={},
        )
        db.add(profile)
        await db.flush()
        secret = await secret_db.store(
            db,
            RING,
            provider=provider,
            owner_type="provider_profile",
            owner_id=profile.id,
            plaintext=key.encode(),
        )
        profile.encrypted_secret_id = secret.id
        await db.commit()
        return profile.id


def use_secret_store(gateway: Gateway, clock: Clock | None = None) -> SecretStoreCredentials:
    creds = SecretStoreCredentials(gateway.sessions, RING, 60.0, clock or Clock())
    gateway.credentials = creds
    gateway.inference.credentials = creds
    return creds


def openai_response(model: str) -> dict[str, Any]:
    return {
        "id": "resp_1",
        "model": model,
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}],
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }


def cloud_body(profile: str, **extra: Any) -> dict[str, Any]:
    return {"profile": profile, "messages": [{"role": "user", "content": "hi"}], **extra}


# --- credential provider ------------------------------------------------------------------


async def test_active_profile_selection_and_decryption(
    gateway: Gateway, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await add_profile(sessions, "openai", "sk-disabled-key-000", enabled=False)
    await add_profile(sessions, "openai", "sk-broken-key-0000", status="ERROR")
    untested = await add_profile(sessions, "openai", OPENAI_KEY, allowed_models=["gpt-a"])
    clock = Clock()
    creds = use_secret_store(gateway, clock)

    profile = await creds.profile("openai")
    assert profile is not None and profile.profile_id == untested
    assert profile.allowed_models == ("gpt-a",)
    assert await creds.api_key("openai") == OPENAI_KEY
    assert await creds.profile("anthropic") is None and await creds.api_key("anthropic") is None
    assert await creds.api_key("google") is None  # the gateway reads only its providers

    # A CONNECTED profile wins over an UNTESTED one.
    connected = await add_profile(sessions, "openai", "sk-connected-key-00", status="CONNECTED")
    chosen = await creds.profile("openai")
    assert chosen is not None and chosen.profile_id == connected

    # Keys are cached briefly, then re-read (a replaced key is picked up).
    assert profile.secret_id is not None
    async with sessions() as db:
        await secret_db.replace(db, RING, profile.secret_id, b"sk-rotated-key-0000")
        await db.commit()
    assert await creds.api_key("openai", profile) == OPENAI_KEY
    clock.now += 61
    assert await creds.api_key("openai", profile) == "sk-rotated-key-0000"
    assert await creds.api_key("openai", profile, cached=False) == "sk-rotated-key-0000"
    assert OPENAI_KEY not in repr(creds) and "rotated" not in repr(creds._cache)


async def test_undecryptable_key_is_not_used_and_not_logged(
    gateway: Gateway, sessions: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
) -> None:
    await add_profile(sessions, "anthropic", ANTHROPIC_KEY)
    wrong = SecretStoreCredentials(gateway.sessions, Keyring({1: bytes(32)}))
    with caplog.at_level(logging.DEBUG):
        assert await wrong.api_key("anthropic") is None
    assert "credentials.decrypt_failed" in [getattr(r, "event", "") for r in caplog.records]
    assert ANTHROPIC_KEY not in caplog.text


def test_keyring_loading_from_settings(tmp_path: Path) -> None:
    assert isinstance(credentials.from_settings(None, None, 60), NoCredentials)  # type: ignore[arg-type]
    good = tmp_path / "master.key"
    good.write_text(f"1:{bytes(range(32)).hex()}\n")
    os.chmod(good, 0o640)
    assert isinstance(credentials.from_settings(None, str(good), 60), SecretStoreCredentials)  # type: ignore[arg-type]
    os.chmod(good, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    with pytest.raises(RuntimeError, match="other users"):
        credentials.from_settings(None, str(good), 60)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="Cannot load"):
        credentials.from_settings(None, str(tmp_path / "missing"), 60)  # type: ignore[arg-type]


def test_gateway_reads_the_shared_master_key_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from crewquarters_gateway.config import GatewaySettings

    monkeypatch.setenv("CQ_MASTER_KEY_FILE", str(tmp_path / "k"))
    assert GatewaySettings().master_key_file == str(tmp_path / "k")


# --- routing with a stored key --------------------------------------------------------------


async def test_cloud_call_uses_stored_key_and_profile_models(
    gw_client: httpx.AsyncClient, gateway: Gateway, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await add_profile(sessions, "openai", OPENAI_KEY, allowed_models=["gpt-test-1", "gpt-test-2"])
    use_secret_store(gateway)
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"auth": request.headers["authorization"], "model": body["model"]})
        return httpx.Response(200, json=openai_response(body["model"]))

    keys = mock_cloud(gateway, openai=handler)
    caps = [
        "cloud.openai",
        "llm.profile:openai.default",
        "llm.profile:openai.gpt-test-2",
        "llm.profile:openai.other",
    ]
    token, _ = run_token(gateway, str(uuid.uuid4()), caps=caps)
    headers = {"X-Capability-Token": token}

    default = await gw_client.post(
        "/internal/v1/llm/chat", json=cloud_body("openai.default"), headers=headers
    )
    assert default.status_code == 200, default.text
    assert default.json()["model"] == "gpt-test-1" and default.json()["locality"] == "cloud"
    named = await gw_client.post(
        "/internal/v1/llm/chat", json=cloud_body("openai.gpt-test-2"), headers=headers
    )
    assert named.status_code == 200 and named.json()["model"] == "gpt-test-2"
    assert seen == [
        {"auth": f"Bearer {OPENAI_KEY}", "model": "gpt-test-1"},
        {"auth": f"Bearer {OPENAI_KEY}", "model": "gpt-test-2"},
    ]
    assert keys == [OPENAI_KEY, OPENAI_KEY]
    assert OPENAI_KEY not in default.text

    unknown = await gw_client.post(
        "/internal/v1/llm/chat", json=cloud_body("openai.other"), headers=headers
    )
    assert unknown.status_code == 409 and unknown.json()["error"]["code"] == "NEEDS_CONFIGURATION"

    # An operator override still has to be one of the owner's allowed models.
    gateway.settings.openai_models = {"other": "gpt-not-allowed"}
    try:
        denied = await gw_client.post(
            "/internal/v1/llm/chat", json=cloud_body("openai.other"), headers=headers
        )
    finally:
        gateway.settings.openai_models = {}
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "PERMISSION_DENIED"


async def test_disabled_or_missing_profile_needs_a_connection(
    gw_client: httpx.AsyncClient, gateway: Gateway, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await add_profile(sessions, "anthropic", ANTHROPIC_KEY, enabled=False)
    use_secret_store(gateway)
    caps = ["cloud.anthropic", "llm.profile:anthropic.default"]
    token, _ = run_token(gateway, str(uuid.uuid4()), caps=caps)
    response = await gw_client.post(
        "/internal/v1/llm/chat",
        json=cloud_body("anthropic.default"),
        headers={"X-Capability-Token": token},
    )
    assert response.status_code == 409
    assert response.json()["error"] | {"requestId": None} == {
        "code": "NEEDS_CONNECTION",
        "message": "No usable anthropic key is configured. Add one in Connections.",
        "requestId": None,
        "details": {"provider": "anthropic"},
    }


async def test_without_a_master_key_cloud_is_disabled_with_a_clear_error(
    gw_client: httpx.AsyncClient, gateway: Gateway
) -> None:
    gateway.inference.credentials = NoCredentials()
    caps = ["cloud.anthropic", "llm.profile:anthropic.default"]
    token, _ = run_token(gateway, str(uuid.uuid4()), caps=caps)
    response = await gw_client.post(
        "/internal/v1/llm/chat",
        json=cloud_body("anthropic.default"),
        headers={"X-Capability-Token": token},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NEEDS_CONNECTION"
    assert "no master key" in response.json()["error"]["message"]


async def test_profile_budgets_apply(
    gw_client: httpx.AsyncClient, gateway: Gateway, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await add_profile(
        sessions,
        "openai",
        OPENAI_KEY,
        allowed_models=["gpt-b"],
        budgets={"dailyTokens": 1000, "perRunTokens": 52},
    )
    use_secret_store(gateway)
    mock_cloud(gateway, openai=lambda r: httpx.Response(200, json=openai_response("gpt-b")))
    run_id = str(uuid.uuid4())
    token, _ = run_token(gateway, run_id, caps=["cloud.openai", "llm.profile:openai.default"])
    headers = {"X-Capability-Token": token}
    body = cloud_body("openai.default", maxOutputTokens=50)

    first = await gw_client.post("/internal/v1/llm/chat", json=body, headers=headers)
    # Each call reserves maxOutputTokens (50) + prompt/4 (0) and then uses 5 tokens.
    assert first.status_code == 200, first.text  # 0 used + 50 reserved <= 52
    per_run = await gw_client.post("/internal/v1/llm/chat", json=body, headers=headers)  # 55 > 52
    assert per_run.status_code == 429
    assert per_run.json()["error"]["code"] == "RUN_TOKEN_BUDGET_EXCEEDED"
    assert per_run.json()["error"]["details"]["limit"] == "perRunTokens"

    async with sessions() as db, db.begin():  # other runs already used most of today's budget
        db.add(
            LlmUsage(
                provider="openai",
                model="gpt-b",
                holder_type="run",
                holder_id="someone-else",
                input_tokens=900,
                output_tokens=90,
                latency_ms=1,
                outcome="ok",
                day=date.today(),
            )
        )
    other, _ = run_token(
        gateway, str(uuid.uuid4()), caps=["cloud.openai", "llm.profile:openai.default"]
    )
    daily = await gw_client.post(
        "/internal/v1/llm/chat", json=body, headers={"X-Capability-Token": other}
    )
    assert daily.status_code == 429 and daily.json()["error"]["code"] == "CLOUD_BUDGET_EXCEEDED"


def test_profile_budget_values_are_validated() -> None:
    profile = CloudProfile(
        provider="openai", budgets={"dailyTokens": "10", "perRunTokens": True, "x": 5}
    )
    assert profile.budget("dailyTokens") is None and profile.budget("perRunTokens") is None
    assert CloudProfile(provider="openai", budgets={"dailyTokens": 7}).budget("dailyTokens") == 7


# --- POST /provider-profiles/{id}/test ---------------------------------------------------------


async def _profile_row(
    sessions: async_sessionmaker[AsyncSession], profile_id: uuid.UUID
) -> ProviderProfile:
    async with sessions() as db:
        row = await db.get(ProviderProfile, profile_id)
        assert row is not None
        return row


async def test_connection_test_marks_openai_connected(
    gw_client: httpx.AsyncClient, gateway: Gateway, sessions: async_sessionmaker[AsyncSession]
) -> None:
    profile_id = await add_profile(sessions, "openai", OPENAI_KEY)
    use_secret_store(gateway)
    calls: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers["authorization"]))
        return httpx.Response(200, json={"object": "list", "data": []})

    mock_cloud(gateway, openai=handler)
    response = await gw_client.post(f"/internal/v1/provider-profiles/{profile_id}/test")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "CONNECTED" and body["detail"] is None
    assert set(body) == {"status", "detail", "checkedAt"} and OPENAI_KEY not in response.text
    assert calls == [("GET", "/v1/models", f"Bearer {OPENAI_KEY}")]
    row = await _profile_row(sessions, profile_id)
    assert row.status == "CONNECTED" and row.last_checked_at is not None
    assert row.last_checked_at.isoformat() == body["checkedAt"]
    async with sessions() as db:
        from sqlalchemy import select

        event = await db.scalar(
            select(AuditEvent).where(AuditEvent.action == "connection.openai.tested")
        )
    assert event is not None and OPENAI_KEY not in json.dumps(event.metadata_)


async def test_connection_test_rejected_key_is_error(
    gw_client: httpx.AsyncClient, gateway: Gateway, sessions: async_sessionmaker[AsyncSession]
) -> None:
    profile_id = await add_profile(sessions, "anthropic", ANTHROPIC_KEY, status="CONNECTED")
    use_secret_store(gateway)
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(f"{request.method} {request.url.path} {request.headers['x-api-key']}")
        return httpx2.Response(
            401,
            json={"type": "error", "error": {"type": "authentication_error", "message": "no"}},
        )

    mock_cloud(gateway, anthropic=handler)
    response = await gw_client.post(f"/internal/v1/provider-profiles/{profile_id}/test")
    assert response.status_code == 200
    assert response.json()["status"] == "ERROR"
    assert response.json()["detail"] == "The provider rejected the key."
    assert seen == [f"GET /v1/models {ANTHROPIC_KEY}"]  # one attempt, no retries
    assert (await _profile_row(sessions, profile_id)).status == "ERROR"
    # An ERROR profile is no longer used for routing.
    assert await gateway.credentials.profile("anthropic") is None


async def test_connection_test_transient_failure_keeps_a_working_profile(
    gw_client: httpx.AsyncClient, gateway: Gateway, sessions: async_sessionmaker[AsyncSession]
) -> None:
    connected = await add_profile(sessions, "openai", OPENAI_KEY, status="CONNECTED")
    untested = await add_profile(sessions, "openai", "sk-second-key-0000")
    use_secret_store(gateway)
    mock_cloud(gateway, openai=lambda r: httpx.Response(503, json={"error": "down"}))
    first = await gw_client.post(f"/internal/v1/provider-profiles/{connected}/test")
    second = await gw_client.post(f"/internal/v1/provider-profiles/{untested}/test")
    assert first.json()["status"] == second.json()["status"] == "ERROR"
    assert "Try again later" in first.json()["detail"]
    assert (await _profile_row(sessions, connected)).status == "CONNECTED"
    assert (await _profile_row(sessions, untested)).status == "ERROR"


async def test_connection_test_unknown_profile_and_auth(
    gw_client: httpx.AsyncClient, gateway: Gateway, sessions: async_sessionmaker[AsyncSession]
) -> None:
    for profile_id in (uuid.uuid4(), "not-a-uuid"):
        response = await gw_client.post(f"/internal/v1/provider-profiles/{profile_id}/test")
        assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"
    real = await add_profile(sessions, "openai", OPENAI_KEY)
    unauthenticated = await gw_client.post(
        f"/internal/v1/provider-profiles/{real}/test", headers={"Authorization": "Bearer nope"}
    )
    assert unauthenticated.status_code == 401


async def test_connection_test_without_master_key(
    gw_client: httpx.AsyncClient, gateway: Gateway, sessions: async_sessionmaker[AsyncSession]
) -> None:
    profile_id = await add_profile(sessions, "openai", OPENAI_KEY)
    gateway.credentials = NoCredentials()
    response = await gw_client.post(f"/internal/v1/provider-profiles/{profile_id}/test")
    assert response.status_code == 200
    assert response.json()["status"] == "ERROR" and "master key" in response.json()["detail"]


async def test_static_credentials_still_work_for_tests(gateway: Gateway) -> None:
    creds = StaticCredentials({"anthropic": "sk-x"})
    assert await creds.profile("anthropic") == CloudProfile(provider="anthropic")
    assert await creds.profile("openai") is None
    assert "sk-x" not in repr(creds)
