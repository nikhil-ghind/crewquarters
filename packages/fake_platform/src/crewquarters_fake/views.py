"""JSON views of store objects, shaped like the control plane's response models.

Control-API views follow ``packages/contracts/openapi.yaml`` (CatalogAgentOut, InstallationOut,
RunOut, InputRequestOut, RunEventOut); broker views follow ``broker-sdk.openapi.yaml``.
"""

from __future__ import annotations

from typing import Any

from crewquarters.redact import mask_phone
from crewquarters_fake.contracts import PLACEHOLDER_DIGEST
from crewquarters_fake.store import (
    ActionRecord,
    CatalogEntry,
    Event,
    InputRequest,
    Installation,
    Run,
    Store,
    VoiceCallRecord,
    iso,
)
from crewquarters_fake.voice.backend import VoiceRoom


def _version_view(entry: CatalogEntry) -> dict[str, Any]:
    spec = entry.manifest["spec"]
    return {
        "id": entry.version_id,
        "version": entry.version,
        "image": spec["image"],
        "imageDigest": entry.image_digest or PLACEHOLDER_DIGEST,
        "sdkProtocol": spec.get("sdkProtocol", "v1alpha1"),
        "architectures": spec["architectures"],
        "triggers": spec["triggers"],
        "permissions": spec["permissions"],
        "resources": spec["resources"],
        "configurationSchema": spec["configurationSchema"],
        "resultSchema": spec.get("resultSchema"),
        "compatible": True,
        "compatibilityIssues": [],
        "createdAt": iso(entry.created_at),
    }


def catalog_view(store: Store, entry: CatalogEntry) -> dict[str, Any]:
    from crewquarters_fake.services import catalog_versions

    versions = catalog_versions(store, entry.agent_id)
    latest = versions[0]
    metadata = latest.manifest["metadata"]
    return {
        "agentId": entry.agent_id,
        "name": metadata["name"],
        "summary": metadata.get("summary", ""),
        "publisher": metadata.get("publisher", ""),
        "source": "imported",
        "trustStatus": entry.trust_status,
        "currentVersion": latest.version,
        "versions": [v.version for v in versions],
        "latest": _version_view(latest),
        "installed": any(i.agent_id == entry.agent_id for i in store.installations.values()),
    }


def installation_view(store: Store, installation: Installation) -> dict[str, Any]:
    entry = store.catalog[(installation.agent_id, installation.version)]
    return {
        "id": installation.id,
        "agentId": installation.agent_id,
        "agentName": installation.manifest["metadata"]["name"],
        "agentVersion": installation.version,
        "agentVersionId": entry.version_id,
        "config": installation.config,
        "requestedPermissions": installation.manifest["spec"]["permissions"],
        "approvedPermissions": installation.approved_permissions,
        "capabilities": sorted(installation.capabilities),
        "modelBindings": installation.model_bindings,
        "needsReapproval": False,
        "enabled": installation.enabled,
        "version": installation.revision,
        "readiness": {"ready": True, "checks": []},
        "createdAt": iso(installation.created_at),
        "updatedAt": iso(installation.created_at),
    }


def run_view(store: Store, run: Run) -> dict[str, Any]:
    installation = store.installations[run.installation_id]
    resources = installation.manifest["spec"]["resources"]
    pending = sum(1 for r in store.inputs.values() if r.run_id == run.id and r.state == "pending")
    return {
        "id": run.id,
        "installationId": run.installation_id,
        "agentId": run.agent_id,
        "agentName": run.agent_name,
        "agentVersion": run.agent_version,
        "trigger": run.trigger,
        "parentRunId": run.parent_run_id,
        "scheduleId": None,
        "scheduledFor": run.scheduled_for,
        "state": run.state,
        "currentAttempt": run.current_attempt,
        "result": run.result,
        "error": run.error,
        "retryable": run.retryable,
        "cancelRequested": run.cancel_requested,
        "acknowledgedAt": None,
        "activeSecondsUsed": 0.0,
        "inputWaitSecondsUsed": round(store.input_wait_used(run), 3),
        "activeTimeoutSeconds": resources["activeTimeoutSeconds"],
        "maxInputWaitSeconds": resources["maxInputWaitSeconds"],
        "usesCloud": bool(installation.manifest["spec"]["permissions"].get("cloudProviders")),
        "pendingInputCount": pending,
        "createdAt": iso(run.created_at),
        "startedAt": iso(run.started_at),
        "finishedAt": iso(run.finished_at),
        "updatedAt": iso(run.updated_at),
    }


def event_view(event: Event) -> dict[str, Any]:
    return {
        "runId": event.run_id,
        "sequence": event.sequence,
        "attempt": event.attempt,
        "type": event.type,
        "payload": event.payload,
        "createdAt": iso(event.created_at),
    }


def input_broker_view(request: InputRequest) -> dict[str, Any]:
    return {
        "id": request.id,
        "key": request.key,
        "state": request.state,
        "version": request.version,
        "createdAt": iso(request.created_at),
        "deadline": iso(request.deadline),
        "answer": request.answer,
        "answeredAt": iso(request.answered_at),
    }


def input_control_view(store: Store, request: InputRequest) -> dict[str, Any]:
    run = store.runs.get(request.run_id)
    return {
        "id": request.id,
        "runId": request.run_id,
        "agentName": run.agent_name if run else None,
        "key": request.key,
        "title": request.title,
        "prompt": request.prompt,
        "schema": request.schema,
        "preview": request.preview,
        "state": request.state,
        "answer": request.answer,
        "deadline": iso(request.deadline),
        "version": request.version,
        "createdAt": iso(request.created_at),
        "answeredAt": iso(request.answered_at),
    }


def action_view(record: ActionRecord, status: str | None = None) -> dict[str, Any]:
    return {"key": record.key, "status": status or record.status, "result": record.result}


def page(items: list[dict[str, Any]], limit: int, cursor: str | None) -> dict[str, Any]:
    start = int(cursor) if cursor and cursor.isdigit() else 0
    chunk = items[start : start + limit]
    next_cursor = str(start + limit) if start + limit < len(items) else None
    return {"items": chunk, "nextCursor": next_cursor}


def voice_call_view(call: VoiceCallRecord, room: VoiceRoom | None) -> dict[str, Any]:
    """The broker's VoiceCall. The full number never appears; the room only while it is useful."""
    active = call.state in {"dialing", "ringing", "answered"}
    return {
        "id": call.id,
        "idempotencyKey": call.idempotency_key,
        "toMasked": mask_phone(call.to),
        "state": call.state,
        "room": (
            {"url": room.url, "name": room.name, "token": room.token, "identity": room.identity}
            if room is not None and active
            else None
        ),
        "calleeIdentity": call.callee_identity,
        "answeredAt": iso(call.answered_at),
        "endedAt": iso(call.ended_at),
        "durationSeconds": call.duration_seconds,
        "errorCode": call.error_code,
        "createdAt": iso(call.created_at),
        "updatedAt": iso(call.updated_at),
    }
