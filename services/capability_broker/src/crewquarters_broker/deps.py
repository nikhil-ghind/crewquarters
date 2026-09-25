"""Application state and FastAPI dependencies."""

from __future__ import annotations

import hmac
from dataclasses import dataclass

import httpx
from crewquarters_secret_store import Keyring
from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_broker.auth import Grant, authorize, bearer
from crewquarters_broker.config import BrokerSettings
from crewquarters_broker.google import GoogleConnector
from crewquarters_broker.internal import InternalClient
from crewquarters_broker.metrics import BrokerMetrics
from crewquarters_broker.twilio import TelephonyService
from crewquarters_shared.errors import PlatformError


class ApiModel(BaseModel):
    """camelCase over HTTP, snake_case in Python (PLAN.md section 4.3)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


@dataclass
class BrokerState:
    settings: BrokerSettings
    keyring: Keyring
    sessions: async_sessionmaker[AsyncSession]
    control: InternalClient
    knowledge: InternalClient
    gateway: httpx.AsyncClient
    google: GoogleConnector
    telephony: TelephonyService
    metrics: BrokerMetrics


def broker_state(request: Request) -> BrokerState:
    state: BrokerState = request.app.state.broker
    return state


async def internal_auth(request: Request, state: BrokerState = Depends(broker_state)) -> None:
    """Service-to-service credential for ``/internal/v1`` routes."""
    expected = state.settings.internal_service_token.get_secret_value()
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(value.encode(), expected.encode()):
        raise PlatformError("UNAUTHENTICATED", "Service credential required.", 401)


async def agent_grant(request: Request, state: BrokerState = Depends(broker_state)) -> Grant:
    return await authorize(
        bearer(request), state.settings.capability_signing_key.get_secret_value(), state.control
    )
