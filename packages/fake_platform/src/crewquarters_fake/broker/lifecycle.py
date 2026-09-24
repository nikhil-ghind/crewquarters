"""Broker lifecycle routes: handshake, heartbeat, events, result."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from crewquarters_contracts.loader import run_event_schema
from crewquarters_fake.broker.auth import RunAuth, require, run_auth
from crewquarters_fake.errors import ApiError
from crewquarters_fake.statemachine import ACTIVE
from crewquarters_fake.store import iso, utcnow

router = APIRouter()
AGENT_EVENT_TYPES = frozenset({"log", "progress", "metric", "artifact"})
_EVENT_VALIDATOR = Draft202012Validator(run_event_schema())


class HandshakeIn(BaseModel):
    protocol: str
    sdkVersion: str
    agentId: str


class AgentEventIn(BaseModel):
    clientEventId: str
    type: str
    occurredAt: str
    payload: dict[str, Any]


class EventsIn(BaseModel):
    events: list[AgentEventIn]


class ResultIn(BaseModel):
    outcome: Literal["succeeded", "failed", "cancelled"]
    result: Any = None
    error: dict[str, Any] | None = None


@router.post("/handshake")
async def handshake(body: HandshakeIn, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        if body.protocol != "v1alpha1":
            raise ApiError(
                409, "PROTOCOL_UNSUPPORTED", f"protocol {body.protocol} is not supported; use v1alpha1"
            )
        require(auth, None, "broker.handshake")
        run, installation = auth.run, auth.installation
        if run.state == "PREPARING":
            auth.store.transition(run, "RUNNING", "agent handshake")
        spec = installation.manifest["spec"]
        permissions = installation.approved_permissions
        connectors = permissions.get("connectors", {})
        remaining = spec["resources"]["maxInputWaitSeconds"] - auth.store.input_wait_used(run)
        await auth.store.notify()
        return {
            "run": {
                "id": run.id,
                "attempt": run.current_attempt,
                "trigger": run.trigger,
                "scheduledFor": run.scheduled_for,
                "installationId": installation.id,
                "agentId": installation.agent_id,
                "agentVersion": installation.version,
                "createdAt": iso(run.created_at),
            },
            "config": installation.config,
            "capabilities": sorted(installation.capabilities),
            "grants": {
                "llmProfiles": installation.resolved_profiles,
                "knowledgeBaseIds": installation.knowledge_base_ids,
                "google": list(connectors.get("google", [])),
                "twilio": list(connectors.get("twilio", [])),
                "cloudProviders": list(permissions.get("cloudProviders", [])),
            },
            "limits": {
                "activeTimeoutSeconds": spec["resources"]["activeTimeoutSeconds"],
                "inputWaitRemainingSeconds": max(0, round(remaining)),
            },
            "heartbeatIntervalSeconds": auth.store.settings.heartbeat_seconds,
            "serverTime": iso(utcnow()),
        }

    return await auth.store.faults.run("broker.handshake", operation)


@router.post("/heartbeat")
async def heartbeat(auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        require(auth, None, "broker.heartbeat")
        return {"cancelRequested": auth.run.state == "CANCELLING", "serverTime": iso(utcnow())}

    return await auth.store.faults.run("broker.heartbeat", operation)


@router.post("/events")
async def post_events(body: EventsIn, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        require(auth, None, "broker.events")
        run = auth.run
        for event in body.events:
            envelope = {
                "runId": run.id,
                "attempt": run.current_attempt,
                "sequence": 1,
                "type": event.type,
                "createdAt": event.occurredAt,
                "payload": event.payload,
            }
            errors = [e.message for e in _EVENT_VALIDATOR.iter_errors(envelope)]
            if event.type not in AGENT_EVENT_TYPES:
                errors.append(f"agents may only emit {sorted(AGENT_EVENT_TYPES)}")
            if errors:
                raise ApiError(
                    422,
                    "INVALID_REQUEST",
                    "invalid event",
                    {"clientEventId": event.clientEventId, "errors": errors},
                )
        accepted = 0
        for event in body.events:
            if event.clientEventId in run.client_event_ids:
                continue
            run.client_event_ids.add(event.clientEventId)
            auth.store.append_event(run, event.type, event.payload)
            accepted += 1
        await auth.store.notify()
        return {"accepted": accepted, "lastSequence": run.sequence}

    return await auth.store.faults.run("broker.events", operation)


@router.post("/result")
async def post_result(body: ResultIn, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        run, store = auth.run, auth.store
        attempt = run.attempt
        assert attempt is not None
        if attempt.outcome is not None:
            if attempt.outcome == body.outcome:
                return {"runState": run.state}
            raise ApiError(
                409, "OUTCOME_ALREADY_RECORDED", f"this attempt already reported {attempt.outcome}"
            )
        require(auth, None, "broker.result")
        if run.state == "CANCELLING":
            store.transition(run, "CANCELLED", f"agent reported {body.outcome} during cancellation")
        else:
            if run.state in ACTIVE - {"RUNNING"}:
                store.transition(run, "RUNNING", "agent reported its outcome")
            if body.outcome == "succeeded":
                store.transition(run, "SUCCEEDED", "agent succeeded")
            elif body.outcome == "failed":
                store.transition(run, "FAILED", (body.error or {}).get("code"))
            else:
                store.transition(run, "CANCELLING", "agent cancelled itself")
                store.transition(run, "CANCELLED", "agent cancelled itself")
        attempt.outcome = body.outcome
        run.result = body.result if body.outcome == "succeeded" else None
        run.error = _normalise_error(body.error) if body.outcome == "failed" else None
        await store.notify()
        return {"runState": run.state}

    return await auth.store.faults.run("broker.result", operation)


def _normalise_error(error: dict[str, Any] | None) -> dict[str, Any]:
    error = error or {}
    return {
        "code": str(error.get("code") or "AGENT_ERROR"),
        "message": str(error.get("message") or ""),
        "retryable": bool(error.get("retryable", False)),
        "details": error.get("details") if isinstance(error.get("details"), dict) else {},
    }
