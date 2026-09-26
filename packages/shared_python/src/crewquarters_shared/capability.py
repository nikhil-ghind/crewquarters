"""Run-scoped capability tokens (PLAN.md section 10.4).

The control API mints a short-lived HS256 token when an attempt starts. The capability
broker and model gateway verify it on every call and additionally check current run
state. Capability strings are derived from the *approved* permission snapshot; the
vocabulary is defined in ``packages/contracts/capabilities.yaml``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from crewquarters_shared.timeutil import utcnow

AUDIENCE = "crewquarters-broker"
ISSUER = "crewquarters-control-api"
ALGORITHM = "HS256"


@dataclass(frozen=True)
class CapabilityClaims:
    token_id: str
    run_id: str
    attempt: int
    installation_id: str
    agent_version_id: str
    capabilities: list[str]
    resources: dict[str, Any]
    issued_at: datetime
    expires_at: datetime


def capabilities_from_permissions(
    permissions: dict[str, Any], model_bindings: dict[str, Any] | None = None
) -> list[str]:
    """Translate an approved manifest ``permissions`` block into capability strings."""
    caps: set[str] = set()
    bindings = model_bindings or {}
    for profile in permissions.get("llmProfiles", []):
        caps.add(f"llm.profile:{bindings.get(profile, profile)}")
    for kb in permissions.get("knowledge", []):
        caps.add(f"knowledge.search:{kb}")
    connectors = permissions.get("connectors", {}) or {}
    for scope in connectors.get("google", []):
        caps.add(f"google.{scope}")
    for op in connectors.get("twilio", []):
        caps.add(f"twilio.{op}")
    for camera in permissions.get("camera", []):
        caps.add(f"camera.snapshot:{camera}")
    for provider in permissions.get("cloudProviders", []):
        caps.add(f"cloud.{provider}")
    if permissions.get("userInput"):
        caps.add("user_input")
    caps.add("events.write")
    caps.add("idempotency")
    return sorted(caps)


def mint(
    *,
    signing_key: str,
    run_id: uuid.UUID,
    attempt: int,
    installation_id: uuid.UUID,
    agent_version_id: uuid.UUID,
    capabilities: list[str],
    resources: dict[str, Any] | None = None,
    ttl_seconds: int,
    now: datetime | None = None,
) -> tuple[str, CapabilityClaims]:
    issued = now or utcnow()
    expires = issued + timedelta(seconds=ttl_seconds)
    token_id = uuid.uuid4().hex
    claims = CapabilityClaims(
        token_id=token_id,
        run_id=str(run_id),
        attempt=attempt,
        installation_id=str(installation_id),
        agent_version_id=str(agent_version_id),
        capabilities=sorted(capabilities),
        resources=resources or {},
        issued_at=issued,
        expires_at=expires,
    )
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "jti": token_id,
        "sub": f"run:{run_id}",
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
        "run": str(run_id),
        "att": attempt,
        "ins": str(installation_id),
        "ver": str(agent_version_id),
        "cap": claims.capabilities,
        "res": claims.resources,
    }
    return jwt.encode(payload, signing_key, algorithm=ALGORITHM), claims


def verify(token: str, signing_key: str) -> CapabilityClaims:
    """Verify signature, audience, issuer, and expiry. Raises ``jwt.InvalidTokenError``."""
    data = jwt.decode(
        token,
        signing_key,
        algorithms=[ALGORITHM],
        audience=AUDIENCE,
        issuer=ISSUER,
        options={"require": ["exp", "iat", "jti", "aud", "iss"]},
    )
    # A correctly signed token can still carry malformed claims; reject it like any other
    # invalid token instead of failing the request with a 500.
    try:
        cap, res, attempt = data["cap"], data.get("res") or {}, data["att"]
        strings = (data["jti"], data["run"], data["ins"], data["ver"])
        if (
            not all(isinstance(s, str) for s in strings)
            or not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or not isinstance(cap, list)
            or not all(isinstance(c, str) for c in cap)
            or not isinstance(res, dict)
        ):
            raise TypeError("malformed capability claims")
        return CapabilityClaims(
            token_id=data["jti"],
            run_id=data["run"],
            attempt=attempt,
            installation_id=data["ins"],
            agent_version_id=data["ver"],
            capabilities=list(cap),
            resources=dict(res),
            issued_at=datetime.fromtimestamp(data["iat"], UTC),
            expires_at=datetime.fromtimestamp(data["exp"], UTC),
        )
    except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
        raise jwt.InvalidTokenError("malformed capability claims") from exc
