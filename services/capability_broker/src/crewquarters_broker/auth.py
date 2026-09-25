"""Capability-token checks on every agent call (PLAN.md section 10.4, ADR 0007).

A call is allowed only when all of these hold:

1. the token's signature, audience, issuer, and expiry are valid;
2. the run is active, and the token is the current attempt's token (``jti``, attempt,
   and installation all match the control API's run view);
3. the capability is in the token AND in the run's current approved permissions
   (the intersection, so a narrowed approval takes effect immediately);
4. the operation's resource is the one the owner configured (spreadsheet, knowledge base);
   capability operations also stop once cancellation is requested;
5. the provider connection is available (checked by the provider adapters).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import jwt
from fastapi import Request

from crewquarters_broker.errors import capability_denied, permission_denied, unauthenticated
from crewquarters_broker.internal import InternalClient
from crewquarters_shared import capability
from crewquarters_shared.errors import PlatformError
from crewquarters_shared.runs.states import ACTIVE_STATES, TERMINAL_STATES


@dataclass(frozen=True)
class Grant:
    claims: capability.CapabilityClaims
    run: dict[str, Any]
    capabilities: frozenset[str]
    # Forwarded to the model gateway, which re-verifies it. Never logged.
    token: str = field(repr=False, default="")

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

    def require(self, cap: str, *, baseline: bool = False) -> None:
        """The capability is granted and, for capability operations, the run is not
        cancelling. Baseline operations (events, actions) keep working after a cancel."""
        if cap not in self.capabilities:
            raise capability_denied(cap)
        if not baseline and self.run.get("cancelRequested"):
            raise PlatformError("RUN_CANCELLED", "The run is being cancelled.", 409)

    def configured(self, key: str, requested: str | None = None) -> str:
        """A resource ID the owner chose in the installation config. When the agent names
        one too, it must be that same resource."""
        value = self.config.get(key)
        if not isinstance(value, str) or not value:
            raise PlatformError(
                "NEEDS_CONFIGURATION", f"The installation config has no {key}.", 409, {"key": key}
            )
        if requested is not None and requested != value:
            raise permission_denied(f"Only the configured {key} may be used.", key=key)
        return value


async def authorize(
    token: str, signing_key: str, control: InternalClient, *, allow_finished: bool = False
) -> Grant:
    """``allow_finished`` (heartbeat and result only) also accepts a run in a terminal
    state, so the agent can learn it must stop and report."""
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
    finished = allow_finished and run.get("state") in TERMINAL_STATES
    if run.get("state") not in ACTIVE_STATES and not finished:
        raise PlatformError("RUN_NOT_ACTIVE", "The run is no longer active.", 409)
    approved = capability.capabilities_from_permissions(
        run.get("permissions") or {}, run.get("modelBindings") or {}
    )
    return Grant(
        claims=claims,
        run=run,
        capabilities=frozenset(claims.capabilities) & frozenset(approved),
        token=token,
    )


def bearer(request: Request) -> str:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise unauthenticated()
    return value
