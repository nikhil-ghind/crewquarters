"""Connection listing and OpenAI/Anthropic provider profiles (the broker never decrypts)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_secret_store import db as secret_db
from crewquarters_secret_store.db import EncryptedSecret, ProviderProfile

if TYPE_CHECKING:
    from broker_testkit import Harness

pytest_plugins = ["broker_testkit"]

PROFILES = "/internal/v1/provider-profiles"
KEY = "placeholder-api-key-aaaaaaaa"


async def test_listing_requires_service_token(harness: Harness) -> None:
    assert (await harness.client.get("/internal/v1/connections")).status_code == 401
    bad = {"authorization": "Bearer wrong"}
    assert (await harness.client.get("/internal/v1/connections", headers=bad)).status_code == 401


async def test_empty_listing_matches_control_api_contract(harness: Harness) -> None:
    resp = await harness.client.get("/internal/v1/connections", headers=harness.service_headers)
    assert [c["provider"] for c in resp.json()] == ["google", "twilio", "openai", "anthropic"]
    for row in resp.json():
        assert row["status"] == "NOT_CONNECTED" and row["grantedCapabilities"] == []
        assert {"provider", "displayName", "status", "grantedCapabilities", "lastCheckedAt"} <= set(
            row
        )


async def test_profile_key_is_encrypted_for_the_gateway(
    harness: Harness, user_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession]
) -> None:
    resp = await harness.client.post(
        PROFILES,
        headers=harness.service_headers,
        json={
            "userId": str(user_id),
            "provider": "openai",
            "displayName": "Team key",
            "apiKey": KEY,
            "allowedModels": ["gpt-x"],
            "budgets": {"dailyTokens": 1000},
        },
    )
    assert resp.status_code == 201, resp.text
    assert KEY not in resp.text
    profile_id = uuid.UUID(resp.json()["id"])
    listing = await harness.client.get(PROFILES, headers=harness.service_headers)
    assert KEY not in listing.text and len(listing.json()) == 1

    async with sessions() as db:
        profile = await db.get(ProviderProfile, profile_id)
        assert profile is not None and profile.encrypted_secret_id is not None
        # What the model gateway does, in-process, with the same keyring.
        plain = await secret_db.load(
            db, harness.app.state.broker.keyring, profile.encrypted_secret_id, provider="openai"
        )
        assert plain.decode() == KEY

    status = await harness.client.get("/internal/v1/connections", headers=harness.service_headers)
    openai = next(c for c in status.json() if c["provider"] == "openai")
    assert openai["status"] == "CONNECTED" and openai["grantedCapabilities"] == ["cloud.openai"]

    gone = await harness.client.delete(
        f"{PROFILES}/{profile_id}",
        params={"userId": str(user_id)},
        headers=harness.service_headers,
    )
    assert gone.status_code == 204
    async with sessions() as db:
        assert (await db.scalars(select(EncryptedSecret))).all() == []
    missing = await harness.client.delete(
        f"{PROFILES}/{profile_id}",
        params={"userId": str(user_id)},
        headers=harness.service_headers,
    )
    assert missing.status_code == 404


async def test_disabled_profile_status(harness: Harness, user_id: uuid.UUID) -> None:
    await harness.client.post(
        PROFILES,
        headers=harness.service_headers,
        json={
            "userId": str(user_id),
            "provider": "anthropic",
            "displayName": "Off",
            "apiKey": KEY,
            "enabled": False,
        },
    )
    status = await harness.client.get("/internal/v1/connections", headers=harness.service_headers)
    anthropic = next(c for c in status.json() if c["provider"] == "anthropic")
    assert anthropic["status"] == "DISABLED" and anthropic["grantedCapabilities"] == []


async def test_profiles_reject_other_providers(harness: Harness, user_id: uuid.UUID) -> None:
    resp = await harness.client.post(
        PROFILES,
        headers=harness.service_headers,
        json={"userId": str(user_id), "provider": "google", "displayName": "x", "apiKey": KEY},
    )
    assert resp.status_code == 422
