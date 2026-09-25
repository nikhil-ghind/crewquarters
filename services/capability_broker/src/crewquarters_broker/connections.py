"""Connection management for the control API (``/internal/v1``, service token only).

The control API owns the public ``/api/v1/connections`` routes and the owner session;
it calls these routes on the owner's behalf. Secret values are accepted here and never
returned.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from crewquarters_broker.deps import ApiModel, BrokerState, broker_state, internal_auth
from crewquarters_secret_store import db as secret_db
from crewquarters_secret_store.db import ProviderProfile
from crewquarters_shared import audit
from crewquarters_shared.errors import PlatformError, conflict, invalid, not_found

router = APIRouter(prefix="/internal/v1", dependencies=[Depends(internal_auth)])

DISPLAY_NAMES = {
    "google": "Google",
    "twilio": "Twilio",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
}


class GoogleStartIn(ApiModel):
    user_id: uuid.UUID
    capabilities: list[Literal["gmail.readonly", "spreadsheets"]] = Field(min_length=1)


class TwilioIn(ApiModel):
    user_id: uuid.UUID
    account_sid: str
    auth_token: str
    from_number: str


class TestCallIn(ApiModel):
    user_id: uuid.UUID
    to: str
    confirm: bool


class ProviderProfileIn(ApiModel):
    user_id: uuid.UUID
    provider: Literal["openai", "anthropic"]
    display_name: str = Field(min_length=1, max_length=100)
    api_key: str = Field(min_length=8, max_length=512)
    allowed_models: list[str] = Field(default_factory=list, max_length=50)
    budgets: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


def _profile_view(profile: ProviderProfile) -> dict[str, Any]:
    return {
        "id": str(profile.id),
        "provider": profile.provider,
        "displayName": profile.display_name,
        "allowedModels": profile.allowed_models,
        "budgets": profile.budgets,
        "enabled": profile.enabled,
        "status": profile.status,
        "lastCheckedAt": profile.last_checked_at,
    }


@router.get("/connections", summary="Connection status for every provider")
async def list_connections(state: BrokerState = Depends(broker_state)) -> list[dict[str, Any]]:
    statuses = {
        "google": await state.google.status(),
        "twilio": await state.telephony.status(),
    }
    async with state.sessions() as db:
        profiles = (
            await db.scalars(
                select(ProviderProfile).where(ProviderProfile.provider.in_(["openai", "anthropic"]))
            )
        ).all()
    for provider in ("openai", "anthropic"):
        mine = [p for p in profiles if p.provider == provider]
        usable = [p for p in mine if p.enabled and p.status != "ERROR"]
        if usable:
            status = "CONNECTED"
        elif not mine:
            status = "NOT_CONNECTED"
        elif not any(p.enabled for p in mine):
            status = "DISABLED"
        else:
            status = "NEEDS_ATTENTION"
        checks = [p.last_checked_at for p in mine if p.last_checked_at]
        statuses[provider] = {
            "status": status,
            "grantedCapabilities": [f"cloud.{provider}"] if usable else [],
            "lastCheckedAt": max(checks) if checks else None,
            # The gateway also uses an untested key; say so until a test confirms it.
            "detail": (
                "Key not tested yet."
                if usable and all(p.status == "UNTESTED" for p in usable)
                else None
            ),
        }
    return [{"provider": p, "displayName": DISPLAY_NAMES[p], **statuses[p]} for p in DISPLAY_NAMES]


@router.post("/connections/google/start", summary="Begin Google consent")
async def google_start(
    body: GoogleStartIn, state: BrokerState = Depends(broker_state)
) -> dict[str, str]:
    """Returns the Google URL to open and a browser binding. The control API must set the
    binding as an HttpOnly cookie ``cq_oauth_binding`` (Path=/api/v1/connections/google)
    before redirecting the owner's browser."""
    return state.google.start(body.user_id, list(body.capabilities))


@router.post("/connections/google/test", summary="Refresh Google access now")
async def google_test(state: BrokerState = Depends(broker_state)) -> dict[str, Any]:
    return await state.google.test()


@router.delete("/connections/google", status_code=204, summary="Revoke and delete Google")
async def google_disconnect(
    user_id: uuid.UUID = Query(alias="userId"), state: BrokerState = Depends(broker_state)
) -> Response:
    await state.google.disconnect(user_id)
    return Response(status_code=204)


@router.put("/connections/twilio", summary="Save Twilio credentials and validate them")
async def twilio_configure(
    body: TwilioIn, state: BrokerState = Depends(broker_state)
) -> dict[str, Any]:
    return await state.telephony.configure(
        body.user_id, body.account_sid, body.auth_token, body.from_number
    )


@router.post("/connections/twilio/test", summary="Validate Twilio without placing a call")
async def twilio_test(state: BrokerState = Depends(broker_state)) -> dict[str, Any]:
    return await state.telephony.test()


@router.post("/connections/twilio/test-call", summary="Place one confirmed test call")
async def twilio_test_call(
    body: TestCallIn, state: BrokerState = Depends(broker_state)
) -> dict[str, Any]:
    """A live call that leaves the device, so the UI must ask the owner first and send
    ``confirm: true``. It speaks a fixed test message only."""
    if not body.confirm:
        raise invalid("CONFIRMATION_REQUIRED", "Confirm the test call first.")
    return await state.telephony.test_call(body.user_id, body.to)


@router.delete("/connections/twilio", status_code=204, summary="Delete Twilio credentials")
async def twilio_disconnect(
    user_id: uuid.UUID = Query(alias="userId"), state: BrokerState = Depends(broker_state)
) -> Response:
    await state.telephony.disconnect(user_id)
    return Response(status_code=204)


@router.get("/provider-profiles", summary="OpenAI and Anthropic profiles (no keys)")
async def list_profiles(state: BrokerState = Depends(broker_state)) -> list[dict[str, Any]]:
    async with state.sessions() as db:
        profiles = (
            await db.scalars(
                select(ProviderProfile)
                .where(ProviderProfile.provider.in_(["openai", "anthropic"]))
                .order_by(ProviderProfile.created_at)
            )
        ).all()
    return [_profile_view(p) for p in profiles]


def _duplicate_profile(name: str) -> PlatformError:
    return conflict(
        "DUPLICATE_PROVIDER_PROFILE", f"A key named {name!r} already exists for this provider."
    )


@router.post("/provider-profiles", status_code=201, summary="Store a cloud API key")
async def create_profile(
    body: ProviderProfileIn, state: BrokerState = Depends(broker_state)
) -> dict[str, Any]:
    """The broker only encrypts the key. The model gateway alone decrypts and tests it."""
    async with state.sessions() as db:
        duplicate = await db.scalar(
            select(ProviderProfile.id).where(
                ProviderProfile.owner_id == body.user_id,
                ProviderProfile.provider == body.provider,
                ProviderProfile.display_name == body.display_name,
            )
        )
        if duplicate is not None:
            raise _duplicate_profile(body.display_name)
        profile = ProviderProfile(
            id=uuid.uuid4(),
            owner_id=body.user_id,
            provider=body.provider,
            display_name=body.display_name,
            allowed_models=body.allowed_models,
            budgets=body.budgets,
            enabled=body.enabled,
            settings={},
        )
        db.add(profile)
        try:
            await db.flush()
        except IntegrityError:  # a concurrent create with the same name
            raise _duplicate_profile(body.display_name) from None
        secret = await secret_db.store(
            db,
            state.keyring,
            provider=body.provider,
            owner_type="provider_profile",
            owner_id=profile.id,
            plaintext=body.api_key.encode(),
        )
        profile.encrypted_secret_id = secret.id
        audit.record(
            db,
            action=f"connection.{body.provider}.saved",
            actor_type="user",
            actor_id=body.user_id,
            target_type="provider_profile",
            target_id=profile.id,
        )
        await db.commit()
        await db.refresh(profile)
        return _profile_view(profile)


@router.delete("/provider-profiles/{profile_id}", status_code=204, summary="Delete a profile")
async def delete_profile(
    profile_id: uuid.UUID,
    user_id: uuid.UUID = Query(alias="userId"),
    state: BrokerState = Depends(broker_state),
) -> Response:
    async with state.sessions() as db:
        profile = await db.get(ProviderProfile, profile_id)
        if profile is None or profile.provider not in ("openai", "anthropic"):
            raise not_found("Provider profile", profile_id)
        secret_id = profile.encrypted_secret_id
        await db.delete(profile)
        await db.flush()
        if secret_id is not None:
            await secret_db.delete(db, secret_id)
        audit.record(
            db,
            action=f"connection.{profile.provider}.deleted",
            actor_type="user",
            actor_id=user_id,
            target_type="provider_profile",
            target_id=profile_id,
        )
        await db.commit()
    return Response(status_code=204)
