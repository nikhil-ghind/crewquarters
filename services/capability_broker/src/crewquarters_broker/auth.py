"""Capability-token checks on every agent call (PLAN.md section 10.4, ADR 0007).

A call is allowed only when all of these hold:

1. the token's signature, audience, issuer, and expiry are valid;
2. the run is active, and the token is the current attempt's token (``jti``, attempt,
   and installation all match the control API's run view);
3. the capability is in the token AND in the run's current approved permissions
   (the intersection, so a narrowed approval takes effect immediately);
4. the operation's resource is the one the owner configured (spreadsheet, knowledge base);
5. the provider connection is available (checked by the provider adapters).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Request

from crewquarters_broker.errors import permission_denied, unauthenticated
from crewquarters_broker.internal import InternalClient
from crewquarters_shared import capability
from crewquarters_shared.errors import PlatformError
from crewquarters_shared.runs.states import ACTIVE_STATES


@dataclass(frozen=True)
class Grant:
    claims: capability.CapabilityClaims
    run: dict[str, Any]
    capabilities: frozenset[str]

    @property
    def run_id(self) -> uuid.UUID:
        return uuid.UUID(self.claims.run_id)

    @property
    def attempt(self) -> int:
        return self.claims.attempt

    @property
    def config(self) -> dict[str, Any]:
        config: dict[str, Any] = self.run.get("config") or {}
        return config

    def require(self, cap: str) -> None:
        if cap not in self.capabilities:
            raise permission_denied(f"This run is not allowed to use {cap}.", capability=cap)

    def require_not_cancelled(self) -> None:
        """Side effects (calls, sheet writes) stop as soon as cancellation is requested."""
        if self.run.get("cancelRequested"):
            raise PlatformError("CANCELLED", "The run is being cancelled.", 409)

    def configured(self, key: str) -> str:
        """A resource ID the owner chose in the installation config."""
        value = self.config.get(key)
        if not isinstance(value, str) or not value:
            raise PlatformError(
                "NEEDS_CONFIGURATION", f"The installation config has no {key}.", 409, {"key": key}
            )
        return value


async def authorize(token: str, signing_key: str, control: InternalClient) -> Grant:
    try:
        claims = capability.verify(token, signing_key)
    except jwt.InvalidTokenError:
        raise unauthenticated() from None
    try:
        run = await control.get_run(claims.run_id)
    except PlatformError as exc:
        if exc.status_code == 404:
            raise unauthenticated() from None
        raise
    if (
        run.get("capabilityTokenId") != claims.token_id
        or run.get("currentAttempt") != claims.attempt
        or run.get("installationId") != claims.installation_id
    ):
        raise unauthenticated("This capability token has been replaced or revoked.")
    if run.get("state") not in ACTIVE_STATES:
        raise PlatformError("RUN_NOT_ACTIVE", "The run is no longer active.", 409)
    approved = capability.capabilities_from_permissions(
        run.get("permissions") or {}, run.get("modelBindings") or {}
    )
    return Grant(
        claims=claims,
        run=run,
        capabilities=frozenset(claims.capabilities) & frozenset(approved),
    )


def bearer(request: Request) -> str:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise unauthenticated()
    return value
