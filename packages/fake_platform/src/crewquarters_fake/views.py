"""JSON views of store objects, shaped by the draft contracts."""

from __future__ import annotations

from typing import Any

from crewquarters_fake.store import (
    CatalogEntry,
    Event,
    IdempotencyRecord,
    InputRequest,
    Installation,
    Run,
    iso,
)


def catalog_view(entry: CatalogEntry) -> dict[str, Any]:
    return {
        "agentId": entry.agent_id,
        "version": entry.version,
        "name": entry.name,
        "imageDigest": entry.image_digest,
        "trustStatus": entry.trust_status,
        "manifest": entry.manifest,
    }


def installation_view(installation: Installation) -> dict[str, Any]:
    return {
        "id": installation.id,
        "agentId": installation.agent_id,
        "version": installation.version,
        "config": installation.config,
        "approvedPermissions": installation.approved_permissions,
        "capabilities": sorted(installation.capabilities),
        "resolvedProfiles": installation.resolved_profiles,
        "knowledgeBaseIds": installation.knowledge_base_ids,
        "enabled": installation.enabled,
        "revision": installation.revision,
    }


def run_view(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "installationId": run.installation_id,
        "agentId": run.agent_id,
        "trigger": run.trigger,
        "scheduledFor": run.scheduled_for,
        "state": run.state,
        "currentAttempt": run.current_attempt,
        "result": run.result,
        "error": run.error,
        "createdAt": iso(run.created_at),
        "updatedAt": iso(run.updated_at),
    }


def event_view(event: Event) -> dict[str, Any]:
    return {
        "runId": event.run_id,
        "attempt": event.attempt,
        "sequence": event.sequence,
        "type": event.type,
        "createdAt": iso(event.created_at),
        "payload": event.payload,
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
    }


def input_control_view(request: InputRequest) -> dict[str, Any]:
    return {
        **input_broker_view(request),
        "runId": request.run_id,
        "agentId": request.agent_id,
        "title": request.title,
        "prompt": request.prompt,
        "schema": request.schema,
        "choices": request.choices or [],
        "preview": request.preview or [],
        "consequence": request.consequence,
    }


def idempotency_view(record: IdempotencyRecord, *, state: str | None = None) -> dict[str, Any]:
    return {
        "key": record.key,
        "state": state or record.state,
        "result": record.result,
        "claimedByAttempt": record.claimed_by_attempt,
        "completedAt": iso(record.completed_at),
    }


def page(items: list[dict[str, Any]], limit: int, cursor: str | None) -> dict[str, Any]:
    start = int(cursor) if cursor and cursor.isdigit() else 0
    chunk = items[start : start + limit]
    next_cursor = str(start + limit) if start + limit < len(items) else None
    return {"items": chunk, "nextCursor": next_cursor}
