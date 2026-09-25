"""Broker lifecycle routes: handshake, heartbeat, events, result.

Each maps onto a control-plane ``/internal/v1/runs/{runId}/...`` route; see
``packages/contracts/broker-sdk.openapi.yaml``.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from crewquarters_fake import contracts, services
from crewquarters_fake.broker.auth import RunAuth, require, run_auth
from crewquarters_fake.errors import ApiError
from crewquarters_fake.store import iso, utcnow

router = APIRouter()
AGENT_EVENT_TYPES = frozenset({"run.log", "run.progress", "run.metric", "run.artifact"})
_EVENT_VALIDATOR = Draft202012Validator(contracts.run_event_schema())


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
    status: Literal["succeeded", "failed"]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


def _state(auth: RunAuth) -> dict[str, Any]:
    return {"state": auth.run.state, "cancelRequested": auth.run.cancel_requested}


@router.post("/handshake")
async def handshake(body: HandshakeIn, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        if body.protocol != "v1alpha1":
            raise ApiError(
                409,
                "PROTOCOL_UNSUPPORTED",
                f"Protocol {body.protocol} is not supported; use v1alpha1.",
            )
        require(auth, None, "broker.handshake")
        run, installation = auth.run, auth.installation
        if run.state == "PREPARING":
            auth.store.transition(run, "RUNNING", None)
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
                "modelBindings": installation.model_bindings,
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
        if auth.run.state in services.TERMINAL:
            # The run is over (for example, it timed out): tell the agent to stop.
            return {"state": auth.run.state, "cancelRequested": True}
        return _state(auth)

    return await auth.store.faults.run("broker.heartbeat", operation)


@router.post("/events")
async def post_events(body: EventsIn, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        require(auth, None, "broker.events")
        run = auth.run
        for event in body.events:
            if event.type not in AGENT_EVENT_TYPES:
                raise ApiError(
                    422,
                    "INVALID_EVENT_TYPE",
                    f"Agents cannot emit {event.type}.",
                    {"clientEventId": event.clientEventId},
                )
            envelope = {
                "runId": run.id,
                "sequence": 1,
                "attempt": run.current_attempt,
                "type": event.type,
                "payload": event.payload,
                "createdAt": event.occurredAt,
            }
            errors = [e.message for e in _EVENT_VALIDATOR.iter_errors(envelope)]
            if errors:
                raise ApiError(
                    422,
                    "INVALID_EVENT",
                    "An event does not match the run-event schema.",
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
        if auth.run.state not in services.TERMINAL:
            require(auth, None, "broker.result")
        services.finish(auth.store, auth.run, body.status, body.result, body.error)
        await auth.store.notify()
        return _state(auth)

    return await auth.store.faults.run("broker.result", operation)
