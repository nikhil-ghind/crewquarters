"""Platform operations shared by the control, broker, and admin routers.

Semantics follow the control plane (``crewquarters_shared.runs.service``): results are
``succeeded`` or ``failed`` and the first result wins; a result posted while ``CANCELLING`` ends the
run ``CANCELLED``; asking with an existing key returns that request (reopening one an earlier
attempt left cancelled or expired); only the first claim of an action key is ``claimed``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import secrets
from datetime import timedelta
from typing import Any

from jsonschema import Draft202012Validator

from crewquarters_fake import contracts
from crewquarters_fake.errors import ApiError
from crewquarters_fake.statemachine import ACTIVE, TRANSITIONS
from crewquarters_fake.store import (
    Attempt,
    AutoAnswer,
    CatalogEntry,
    InputRequest,
    Installation,
    Run,
    Store,
    new_id,
    utcnow,
)
from crewquarters_shared.errors import PlatformError
from crewquarters_shared.schema_guard import check_schema

TERMINAL = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"})
CANCELLABLE = frozenset({"QUEUED", "PREPARING", "LOADING_MODEL", "RUNNING", "WAITING_INPUT"})


def from_platform_error(exc: PlatformError) -> ApiError:
    return ApiError(exc.status_code, exc.code, exc.message, exc.details)


# --- catalog and installations --------------------------------------------------------------


def import_manifest(store: Store, manifest: Any, *, allow_unbuilt: bool) -> CatalogEntry:
    if not isinstance(manifest, dict):
        raise ApiError(422, "INVALID_MANIFEST", "The agent manifest must be an object.")
    normalized, issues = contracts.validate_manifest(manifest, allow_unbuilt=allow_unbuilt)
    if normalized is None:
        raise ApiError(
            422,
            "INVALID_MANIFEST",
            "The agent manifest is invalid.",
            {"errors": [{"path": i.path, "message": i.message} for i in issues]},
        )
    agent_id, version = normalized["metadata"]["id"], normalized["metadata"]["version"]
    existing = store.catalog.get((agent_id, version))
    if existing is not None:
        if existing.manifest != normalized:
            raise ApiError(
                409,
                "AGENT_VERSION_IMMUTABLE",
                f"{agent_id} {version} already exists with a different manifest.",
                {"agentId": agent_id, "version": version},
            )
        return existing
    entry = CatalogEntry(
        agent_id=agent_id,
        version=version,
        name=normalized["metadata"]["name"],
        manifest=normalized,
        image_digest=contracts.image_digest(normalized["spec"]["image"]),
        trust_status="imported_unreviewed",
    )
    store.catalog[(agent_id, version)] = entry
    return entry


def _version_key(version: str) -> tuple[int, ...]:
    core = version.split("-", 1)[0]
    return tuple(int(part) for part in core.split(".") if part.isdigit())


def catalog_versions(store: Store, agent_id: str) -> list[CatalogEntry]:
    entries = [e for (aid, _), e in store.catalog.items() if aid == agent_id]
    return sorted(entries, key=lambda e: _version_key(e.version), reverse=True)


def _normalize_permissions(permissions: dict[str, Any]) -> dict[str, Any]:
    connectors = permissions.get("connectors") or {}
    return {
        "llmProfiles": sorted(permissions.get("llmProfiles", [])),
        "knowledge": sorted(permissions.get("knowledge", [])),
        "connectors": {
            "google": sorted(connectors.get("google", [])),
            "twilio": sorted(connectors.get("twilio", [])),
        },
        "cloudProviders": sorted(permissions.get("cloudProviders", [])),
        "userInput": bool(permissions.get("userInput", False)),
    }


def _knowledge_base_ids(manifest: dict[str, Any], config: dict[str, Any]) -> list[str]:
    """``knowledge: [config]`` grants the knowledge base selected in the installation config."""
    if "config" not in manifest["spec"]["permissions"].get("knowledge", []):
        return []
    schema = manifest["spec"]["configurationSchema"]
    ids = []
    for name, prop in (schema.get("properties") or {}).items():
        value = config.get(name)
        widget = isinstance(prop, dict) and prop.get("x-crewquarters-widget") == "knowledgeBase"
        if (widget or name == "knowledgeBaseId") and isinstance(value, str) and value:
            ids.append(value)
    return ids


def install(
    store: Store,
    agent_id: str,
    version: str | None,
    config: dict[str, Any],
    approved: dict[str, Any],
    model_bindings: dict[str, str] | None,
) -> Installation:
    versions = catalog_versions(store, agent_id)
    if not versions:
        raise ApiError(404, "NOT_FOUND", "Agent not found.", {"id": agent_id})
    entry = next((e for e in versions if e.version == version), None) if version else versions[0]
    if entry is None:
        raise ApiError(
            404, "NOT_FOUND", "Agent version not found.", {"id": f"{agent_id}@{version}"}
        )
    manifest = entry.manifest
    requested = manifest["spec"]["permissions"]
    if _normalize_permissions(approved) != _normalize_permissions(requested):
        raise ApiError(
            422,
            "PERMISSIONS_NOT_APPROVED",
            "Approve exactly the permissions this agent version requests.",
            {"requested": requested},
        )
    try:
        bindings = contracts.model_bindings(list(requested.get("llmProfiles", [])), model_bindings)
        filled = contracts.validate_config(manifest, config, bindings)
    except PlatformError as exc:
        raise from_platform_error(exc) from exc
    installation = Installation(
        id=new_id(),
        agent_id=agent_id,
        version=entry.version,
        manifest=manifest,
        config=filled,
        approved_permissions=requested,
        capabilities=frozenset(contracts.capabilities(requested, bindings)),
        model_bindings=bindings,
        knowledge_base_ids=_knowledge_base_ids(manifest, filled),
    )
    store.installations[installation.id] = installation
    return installation


# --- runs -----------------------------------------------------------------------------------


def get_run(store: Store, run_id: str) -> Run:
    run = store.runs.get(run_id)
    if run is None:
        raise ApiError(404, "NOT_FOUND", "Run not found.", {"id": run_id})
    return run


def create_run(
    store: Store,
    installation_id: str,
    trigger: str,
    scheduled_for: str | None,
    idempotency_key: str | None,
) -> Run:
    if idempotency_key and idempotency_key in store.run_keys:
        return store.runs[store.run_keys[idempotency_key]]
    installation = store.installations.get(installation_id)
    if installation is None:
        raise ApiError(404, "NOT_FOUND", "Installation not found.", {"id": installation_id})
    if trigger not in installation.manifest["spec"]["triggers"]:
        raise ApiError(
            422, "TRIGGER_NOT_ALLOWED", f"{installation.agent_id} does not support {trigger} runs."
        )
    if trigger == "schedule" and not scheduled_for:
        raise ApiError(422, "INVALID_REQUEST", "scheduledFor is required for schedule runs.")
    now = utcnow()
    run = Run(
        id=new_id(),
        installation_id=installation.id,
        agent_id=installation.agent_id,
        agent_version=installation.version,
        agent_name=installation.manifest["metadata"]["name"],
        trigger=trigger,
        scheduled_for=scheduled_for if trigger == "schedule" else None,
        created_at=now,
        updated_at=now,
    )
    store.runs[run.id] = run
    store.append_event(run, "run.state_changed", {"from": None, "to": "QUEUED"})
    if idempotency_key:
        store.run_keys[idempotency_key] = run.id
    return run


def dispatch(store: Store, run_id: str, broker_url: str) -> dict[str, Any]:
    run = get_run(store, run_id)
    if run.state != "QUEUED":
        raise ApiError(
            409, "INVALID_RUN_STATE", f"Only QUEUED runs can be dispatched; run is {run.state}."
        )
    token = secrets.token_urlsafe(32)
    run.attempts[run.current_attempt] = Attempt(number=run.current_attempt, token=token)
    store.tokens[token] = (run.id, run.current_attempt)
    store.transition(run, "PREPARING")
    spec = store.installations[run.installation_id].manifest["spec"]
    return {
        "runId": run.id,
        "attempt": run.current_attempt,
        "env": {
            "PLATFORM_BROKER_URL": broker_url,
            "PLATFORM_RUN_TOKEN": token,
            "PLATFORM_RUN_ID": run.id,
            "PLATFORM_ATTEMPT": str(run.current_attempt),
        },
        "image": spec["image"],
        "entrypoint": spec["entrypoint"],
        "resources": spec["resources"],
    }


def _fail(store: Store, run: Run, error: dict[str, Any], target: str = "FAILED") -> None:
    run.error = error
    run.retryable = bool(error.get("retryable", True))
    store.transition(run, target)


def fail_run(
    store: Store, run: Run, code: str, message: str, *, retryable: bool, interrupted: bool
) -> None:
    """A platform-side failure, like the control plane's ``fail_run``."""
    target = "INTERRUPTED" if interrupted else "FAILED"
    if run.state in TERMINAL or target not in TRANSITIONS[run.state]:
        return
    error = {"code": code, "message": message, "retryable": retryable}
    store.append_event(run, "run.error", error)
    _fail(store, run, error, target)
    close_pending_inputs(store, run, "cancelled")


def report_exit(store: Store, run_id: str, attempt: int, exit_code: int) -> Run:
    """The launcher saw the container exit. The control plane learns this from the missing
    heartbeat (``HEARTBEAT_LOST``) or the stop job (cancellation); the fake is told directly."""
    run = get_run(store, run_id)
    if attempt != run.current_attempt or run.attempt is None:
        raise ApiError(409, "STALE_ATTEMPT", f"Attempt {attempt} is not the current attempt.")
    run.attempt.exit_code = exit_code
    if run.attempt.outcome is None:
        if run.state == "CANCELLING":
            store.transition(run, "CANCELLED", "container_stopped")
        elif run.state in ACTIVE:
            fail_run(
                store,
                run,
                "HEARTBEAT_LOST",
                f"The agent exited with code {exit_code} before reporting a result.",
                retryable=True,
                interrupted=True,
            )
    return run


def finish(
    store: Store,
    run: Run,
    status: str,
    result: dict[str, Any] | None,
    error: dict[str, Any] | None,
) -> Run:
    """Record the attempt's result. The first result wins."""
    assert run.attempt is not None
    if run.state in TERMINAL:
        return run
    run.attempt.outcome = status
    run.result = result
    if run.state == "CANCELLING":
        store.transition(run, "CANCELLED", "finished_during_cancel")
        return run
    if run.state in {"LOADING_MODEL", "WAITING_INPUT"}:
        store.transition(run, "RUNNING", "finishing")
    store.append_event(run, "run.result", {"status": status})
    if status == "succeeded":
        store.transition(run, "SUCCEEDED")
    else:
        err = error or {"code": "AGENT_FAILED", "message": "The agent reported a failure."}
        _fail(store, run, {**err, "retryable": bool(err.get("retryable", True))})
    close_pending_inputs(store, run, "cancelled")
    return run


def cancel_run(store: Store, run_id: str) -> Run:
    run = get_run(store, run_id)
    if run.state not in CANCELLABLE:
        if run.state in {"CANCELLING", "CANCELLED"}:
            return run
        raise ApiError(409, "RUN_NOT_CANCELLABLE", f"A {run.state} run cannot be cancelled.")
    run.cancel_requested = True
    if run.state == "QUEUED":
        store.transition(run, "CANCELLED", "owner_cancel")
    else:
        store.transition(run, "CANCELLING", "owner_cancel")
    close_pending_inputs(store, run, "cancelled")
    return run


def retry_run(store: Store, run_id: str) -> Run:
    run = get_run(store, run_id)
    if run.state == "FAILED" and not run.retryable:
        raise ApiError(409, "RUN_NOT_RETRYABLE", "This failure is not retryable.")
    if run.state not in {"FAILED", "INTERRUPTED"}:
        raise ApiError(409, "RUN_NOT_RETRYABLE", f"A {run.state} run cannot be retried.")
    run.current_attempt += 1
    run.error = None
    run.result = None
    run.retryable = False
    run.cancel_requested = False
    # Both time limits are per attempt.
    run.waited_seconds = 0.0
    store.transition(run, "QUEUED", "owner_retry")
    return run


# --- input requests -------------------------------------------------------------------------


def pending_inputs(store: Store, run: Run) -> list[InputRequest]:
    return [r for r in store.inputs.values() if r.run_id == run.id and r.state == "pending"]


def _input_event(store: Store, request: InputRequest, event_type: str) -> None:
    run = store.runs[request.run_id]
    payload: dict[str, Any] = {"inputRequestId": request.id, "key": request.key}
    if event_type == "run.input_requested":
        payload["title"] = request.title
    store.append_event(run, event_type, {**payload, "state": request.state})


def resume_if_no_pending(store: Store, run: Run) -> None:
    if run.state == "WAITING_INPUT" and not pending_inputs(store, run):
        store.transition(run, "RUNNING", "input_closed")


def close_pending_inputs(store: Store, run: Run, state: str) -> None:
    for request in pending_inputs(store, run):
        request.state = state
        request.version += 1
        _input_event(store, request, "run.input_closed")


def ask(
    store: Store,
    run: Run,
    *,
    key: str,
    title: str,
    prompt: str,
    schema: dict[str, Any],
    timeout_seconds: int,
    preview: dict[str, Any] | None,
) -> InputRequest:
    attempt = run.current_attempt
    if run.state not in {"RUNNING", "WAITING_INPUT"}:
        raise ApiError(409, "INVALID_RUN_STATE", f"Cannot ask for input while {run.state}.")
    existing_id = store.input_keys.get((run.id, key))
    existing = store.inputs.get(existing_id) if existing_id else None
    if existing is not None:
        expire_if_due(store, existing)
        reopen = existing.state in {"cancelled", "expired"} and existing.attempt < attempt
        if not reopen:
            if existing.state == "pending" and run.state == "RUNNING":
                store.transition(run, "WAITING_INPUT", f"input:{key}")
            return existing
    try:
        check_schema(schema, code="INVALID_INPUT_SCHEMA", allow_patterns=False)
    except PlatformError as exc:
        raise from_platform_error(exc) from exc
    resources = store.installations[run.installation_id].manifest["spec"]["resources"]
    remaining = resources["maxInputWaitSeconds"] - store.input_wait_used(run)
    if timeout_seconds <= 0 or timeout_seconds > remaining:
        raise ApiError(
            422,
            "INPUT_WAIT_BUDGET_EXCEEDED",
            "timeoutSeconds exceeds the run's remaining input-wait budget.",
            {"remainingSeconds": int(max(0, remaining))},
        )
    now = utcnow()
    if existing is not None:
        existing.state = "pending"
        existing.attempt = attempt
        existing.title, existing.prompt = title, prompt
        existing.schema, existing.preview = schema, preview
        existing.deadline = now + timedelta(seconds=timeout_seconds)
        existing.version += 1
        request = existing
    else:
        request = InputRequest(
            id=new_id(),
            run_id=run.id,
            agent_id=run.agent_id,
            key=key,
            attempt=attempt,
            title=title,
            prompt=prompt,
            schema=schema,
            preview=preview,
            timeout_seconds=timeout_seconds,
            created_at=now,
            deadline=now + timedelta(seconds=timeout_seconds),
        )
        store.inputs[request.id] = request
        store.input_keys[(run.id, key)] = request.id
    _input_event(store, request, "run.input_requested")
    if run.state == "RUNNING":
        store.transition(run, "WAITING_INPUT", f"input:{key}")
    schedule_auto_answer(store, request)
    return request


def expire_if_due(store: Store, request: InputRequest) -> bool:
    if request.state != "pending" or utcnow() < request.deadline:
        return False
    request.state = "expired"
    request.version += 1
    _input_event(store, request, "run.input_closed")
    resume_if_no_pending(store, store.runs[request.run_id])
    return True


def answer_input(store: Store, request_id: str, version: int, value: Any) -> InputRequest:
    """Checks in the control plane's order: closed, version, deadline, schema."""
    request = store.inputs.get(request_id)
    if request is None:
        raise ApiError(404, "NOT_FOUND", "Input request not found.", {"id": request_id})
    if request.state != "pending":
        raise ApiError(409, "INPUT_ALREADY_CLOSED", f"This request is already {request.state}.")
    if version != request.version:
        raise ApiError(
            409,
            "VERSION_CONFLICT",
            "The request changed; reload it.",
            {"currentVersion": request.version},
        )
    if request.deadline <= utcnow():
        expire_if_due(store, request)
        raise ApiError(409, "INPUT_EXPIRED", "This request has expired.")
    errors = [e.message for e in Draft202012Validator(request.schema).iter_errors(value)][:10]
    if errors:
        raise ApiError(
            422,
            "INVALID_ANSWER",
            "The answer does not match the requested form.",
            {"errors": errors},
        )
    request.state = "answered"
    request.version += 1
    request.answer = value
    request.answered_at = utcnow()
    _input_event(store, request, "run.input_answered")
    resume_if_no_pending(store, store.runs[request.run_id])
    return request


def schedule_auto_answer(store: Store, request: InputRequest) -> None:
    for rule in store.auto_answers:
        if fnmatch.fnmatchcase(request.key, rule.key_pattern):
            store.spawn(_auto_answer(store, request.id, rule))
            return


async def _auto_answer(store: Store, request_id: str, rule: AutoAnswer) -> None:
    await asyncio.sleep(rule.delay_seconds)
    request = store.inputs.get(request_id)
    if request is None or request.state != "pending":
        return
    try:
        answer_input(store, request_id, request.version, rule.value)
    except ApiError:
        return
    await store.notify()
