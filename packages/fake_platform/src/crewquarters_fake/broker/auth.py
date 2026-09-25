"""Run-token authentication and capability checks for broker routes."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from crewquarters_fake.errors import ApiError
from crewquarters_fake.statemachine import ACTIVE
from crewquarters_fake.store import Installation, Run, Store


@dataclass
class RunAuth:
    store: Store
    run: Run
    attempt: int
    installation: Installation


async def run_auth(request: Request) -> RunAuth:
    store: Store = request.app.state.store
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    ref = store.tokens.get(token)
    if ref is None:
        raise ApiError(401, "UNAUTHENTICATED", "unknown or missing run token")
    run_id, attempt = ref
    run = store.runs[run_id]
    if attempt != run.current_attempt:
        raise ApiError(401, "UNAUTHENTICATED", "this token belongs to a superseded attempt")
    return RunAuth(store, run, attempt, store.installations[run.installation_id])


def require(auth: RunAuth, capability: str | None, operation: str) -> None:
    """Check run state and capability for an operation (spec section 4.3)."""
    state = auth.run.state
    if state == "CANCELLING":
        if capability is not None:
            raise ApiError(409, "RUN_CANCELLED", "the run is being cancelled")
        return
    if state not in ACTIVE:
        raise ApiError(409, "RUN_NOT_ACTIVE", f"the run is {state}")
    if capability is not None and capability not in auth.installation.capabilities:
        deny(auth, capability, operation)


def deny(auth: RunAuth, capability: str, operation: str) -> None:
    """Record the denial in the audit log and refuse the call."""
    auth.store.audit_event(
        auth.run, "capability.denied", {"capability": capability, "operation": operation}
    )
    raise ApiError(403, "CAPABILITY_DENIED", f"{operation} requires the {capability} capability")
