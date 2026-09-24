"""Platform operations shared by the control, broker, and admin routers."""

from __future__ import annotations

import asyncio
import fnmatch
import secrets
from datetime import timedelta
from typing import Any

from jsonschema import Draft202012Validator

from crewquarters_contracts.manifest import (
    apply_config_defaults,
    config_issues,
    derive_capabilities,
    image_digest,
    validate_manifest,
    widget_values,
)
from crewquarters_contracts.profiles import resolve_profiles
from crewquarters_fake.errors import ApiError
from crewquarters_fake.statemachine import ACTIVE
from crewquarters_fake.store import (
    Attempt,
    AutoAnswer,
    CatalogEntry,
    InputRequest,
    Installation,
    Run,
    Store,
    iso,
    new_id,
    utcnow,
)


def import_manifest(store: Store, manifest: Any, *, allow_unbuilt: bool) -> CatalogEntry:
    if not isinstance(manifest, dict):
        raise ApiError(422, "MANIFEST_INVALID", "manifest must be an object")
    issues = validate_manifest(manifest, allow_unbuilt=allow_unbuilt)
    if issues:
        raise ApiError(
            422,
            "MANIFEST_INVALID",
            "manifest failed validation",
            {"issues": [{"path": i.path, "message": i.message} for i in issues]},
        )
    agent_id, version = manifest["metadata"]["id"], manifest["metadata"]["version"]
    digest = image_digest(manifest["spec"]["image"])
    existing = store.catalog.get((agent_id, version))
    if existing is not None and existing.image_digest != digest:
        raise ApiError(
            409,
            "VERSION_CONFLICT",
            f"{agent_id} {version} is already in the catalog with a different image digest; bump the version",
        )
    entry = CatalogEntry(
        agent_id=agent_id,
        version=version,
        name=manifest["metadata"]["name"],
        manifest=manifest,
        image_digest=digest,
        trust_status="local-import" if digest else "unbuilt-dev",
    )
    store.catalog[(agent_id, version)] = entry
    return entry


def _not_requested(requested: dict[str, Any], approved: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if approved.get("userInput") and not requested.get("userInput"):
        problems.append("userInput")
    for name in ("llmProfiles", "knowledge", "cloudProviders"):
        extra = set(approved.get(name, [])) - set(requested.get(name, []))
        problems.extend(f"{name}: {item}" for item in sorted(extra))
    requested_connectors = requested.get("connectors", {})
    for connector, scopes in approved.get("connectors", {}).items():
        extra = set(scopes) - set(requested_connectors.get(connector, []))
        problems.extend(f"connectors.{connector}: {scope}" for scope in sorted(extra))
    return problems


def install(
    store: Store, agent_id: str, version: str, config: dict[str, Any], approved: dict[str, Any] | None
) -> Installation:
    entry = store.catalog.get((agent_id, version))
    if entry is None:
        raise ApiError(404, "NOT_FOUND", f"{agent_id} {version} is not in the catalog")
    spec = entry.manifest["spec"]
    requested = spec["permissions"]
    approved = requested if approved is None else approved
    problems = _not_requested(requested, approved)
    if problems:
        raise ApiError(
            422,
            "PERMISSION_NOT_REQUESTED",
            "approved permissions exceed the manifest",
            {"permissions": problems},
        )
    schema = spec["configurationSchema"]
    filled = apply_config_defaults(schema, config)
    issues = config_issues(schema, filled)
    if issues:
        raise ApiError(
            422,
            "CONFIG_INVALID",
            "configuration does not match the agent's configuration schema",
            {"issues": [{"path": i.path, "message": i.message} for i in issues]},
        )
    model_profiles = widget_values(schema, filled, "modelProfile")
    try:
        resolved = resolve_profiles(
            list(approved.get("llmProfiles", [])), model_profiles[0] if model_profiles else None
        )
    except ValueError as exc:
        raise ApiError(422, "CONFIG_INVALID", str(exc)) from exc
    installation = Installation(
        id=new_id("inst"),
        agent_id=agent_id,
        version=version,
        manifest=entry.manifest,
        config=filled,
        approved_permissions=approved,
        capabilities=derive_capabilities(approved),
        resolved_profiles=resolved,
        knowledge_base_ids=widget_values(schema, filled, "knowledgeBase"),
    )
    store.installations[installation.id] = installation
    return installation


def get_run(store: Store, run_id: str) -> Run:
    run = store.runs.get(run_id)
    if run is None:
        raise ApiError(404, "NOT_FOUND", f"run {run_id} does not exist")
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
        raise ApiError(404, "NOT_FOUND", f"installation {installation_id} does not exist")
    if trigger not in installation.manifest["spec"]["triggers"]:
        raise ApiError(422, "TRIGGER_NOT_ALLOWED", f"{installation.agent_id} does not support {trigger} runs")
    if trigger == "schedule" and not scheduled_for:
        raise ApiError(422, "INVALID_REQUEST", "scheduledFor is required for schedule runs")
    now = utcnow()
    run = Run(
        id=new_id("run"),
        installation_id=installation.id,
        agent_id=installation.agent_id,
        agent_version=installation.version,
        trigger=trigger,
        scheduled_for=scheduled_for if trigger == "schedule" else None,
        created_at=now,
        updated_at=now,
    )
    store.runs[run.id] = run
    if idempotency_key:
        store.run_keys[idempotency_key] = run.id
    return run


def dispatch(store: Store, run_id: str, broker_url: str) -> dict[str, Any]:
    run = get_run(store, run_id)
    if run.state != "QUEUED":
        raise ApiError(409, "INVALID_STATE", f"only QUEUED runs can be dispatched; run is {run.state}")
    token = secrets.token_urlsafe(32)
    run.attempts.append(Attempt(number=run.current_attempt + 1, token=token))
    store.tokens[token] = (run.id, run.current_attempt)
    store.transition(run, "PREPARING", f"attempt {run.current_attempt} dispatched")
    spec = store.installations[run.installation_id].manifest["spec"]
    return {
        "runId": run.id,
        "attempt": run.current_attempt,
        "env": {"PLATFORM_BROKER_URL": broker_url, "PLATFORM_RUN_TOKEN": token, "PLATFORM_RUN_ID": run.id},
        "image": spec["image"],
        "entrypoint": spec["entrypoint"],
        "resources": spec["resources"],
    }


def report_exit(store: Store, run_id: str, attempt: int, exit_code: int) -> Run:
    run = get_run(store, run_id)
    if attempt != run.current_attempt or run.attempt is None:
        raise ApiError(409, "INVALID_STATE", f"attempt {attempt} is not the current attempt")
    run.attempt.exit_code = exit_code
    if run.attempt.outcome is None:
        if run.state == "CANCELLING":
            store.transition(run, "CANCELLED", "agent exited during cancellation")
        elif run.state in ACTIVE:
            run.error = {
                "code": "CONTAINER_EXITED",
                "message": f"the agent exited with code {exit_code} before reporting an outcome",
                "retryable": True,
                "details": {},
            }
            store.transition(run, "INTERRUPTED", "agent exited without an outcome")
    return run


def pending_inputs(store: Store, run: Run) -> list[InputRequest]:
    return [r for r in store.inputs.values() if r.run_id == run.id and r.state == "pending"]


def resume_if_no_pending(store: Store, run: Run) -> None:
    if run.state == "WAITING_INPUT" and not pending_inputs(store, run):
        store.transition(run, "RUNNING", "input settled")


def cancel_run(store: Store, run_id: str) -> Run:
    run = get_run(store, run_id)
    if run.state == "QUEUED":
        store.transition(run, "CANCELLED", "cancelled by owner")
    elif run.state in ACTIVE:
        for request in pending_inputs(store, run):
            request.state = "cancelled"
            request.version += 1
        store.transition(run, "CANCELLING", "cancelled by owner")
    else:
        raise ApiError(409, "INVALID_STATE", f"a {run.state} run cannot be cancelled")
    return run


def retry_run(store: Store, run_id: str) -> Run:
    run = get_run(store, run_id)
    retryable = run.state == "INTERRUPTED" or (
        run.state == "FAILED" and bool(run.error and run.error.get("retryable"))
    )
    if not retryable:
        raise ApiError(409, "NOT_RETRYABLE", f"a {run.state} run with this error cannot be retried")
    run.error = None
    run.result = None
    store.transition(run, "QUEUED", "retried by owner")
    return run


def expire_if_due(store: Store, request: InputRequest) -> bool:
    if request.state != "pending" or utcnow() < request.deadline:
        return False
    request.state = "expired"
    request.version += 1
    run = store.runs[request.run_id]
    resume_if_no_pending(store, run)
    return True


def answer_input(store: Store, request_id: str, version: int, data: Any, answered_by: str) -> InputRequest:
    request = store.inputs.get(request_id)
    if request is None:
        raise ApiError(404, "NOT_FOUND", f"input request {request_id} does not exist")
    expire_if_due(store, request)
    if request.state == "answered":
        raise ApiError(409, "ALREADY_ANSWERED", "this request has already been answered")
    if request.state != "pending":
        raise ApiError(409, "INVALID_STATE", f"this request is {request.state}")
    if version != request.version:
        raise ApiError(409, "VERSION_CONFLICT", f"expected version {request.version}")
    errors = [e.message for e in Draft202012Validator(request.schema).iter_errors(data)]
    if errors:
        raise ApiError(
            422, "ANSWER_INVALID", "the answer does not match the request schema", {"errors": errors}
        )
    request.state = "answered"
    request.version += 1
    request.answer = {"data": data, "answeredAt": iso(utcnow()), "answeredBy": answered_by}
    run = store.runs[request.run_id]
    store.append_event(
        run, "input.answered", {"inputRequestId": request.id, "key": request.key, "answeredBy": answered_by}
    )
    resume_if_no_pending(store, run)
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
        answer_input(store, request_id, request.version, rule.data, "auto-answer")
    except ApiError:
        return
    await store.notify()


def input_deadline(timeout_seconds: int) -> Any:
    return utcnow() + timedelta(seconds=timeout_seconds)
