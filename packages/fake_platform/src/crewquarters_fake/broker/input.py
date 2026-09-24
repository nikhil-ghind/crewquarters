"""Broker input-request routes."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field

from crewquarters_fake import services
from crewquarters_fake.broker.auth import RunAuth, require, run_auth
from crewquarters_fake.errors import ApiError
from crewquarters_fake.store import InputRequest, new_id, utcnow
from crewquarters_fake.views import input_broker_view

router = APIRouter()


class ChoiceIn(BaseModel):
    value: str
    label: str
    style: str = "secondary"


class InputCreateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1)
    answer_schema: dict[str, Any] = Field(alias="schema")
    choices: list[ChoiceIn] | None = None
    preview: list[dict[str, Any]] | None = None
    consequence: str | None = None
    timeoutSeconds: int = Field(ge=1, le=86400)


def _content_hash(body: InputCreateIn) -> str:
    content = {
        "title": body.title,
        "prompt": body.prompt,
        "schema": body.answer_schema,
        "choices": [c.model_dump() for c in body.choices] if body.choices else None,
        "preview": body.preview,
        "consequence": body.consequence,
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


@router.post("/input-requests")
async def create_input(body: InputCreateIn, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        require(auth, "input.ask", "broker.input.create")
        run, store = auth.run, auth.store
        digest = _content_hash(body)
        existing_id = store.input_keys.get((run.id, body.key))
        if existing_id is not None:
            existing = store.inputs[existing_id]
            if existing.content_hash != digest:
                raise ApiError(
                    409, "INPUT_KEY_CONFLICT", f"key {body.key} was already used with different content"
                )
            services.expire_if_due(store, existing)
            if existing.state == "pending" and run.state == "RUNNING":
                store.transition(run, "WAITING_INPUT", f"waiting for {existing.key}")
            await store.notify()
            return input_broker_view(existing)
        try:
            Draft202012Validator.check_schema(body.answer_schema)
        except SchemaError as exc:
            raise ApiError(
                422, "INVALID_REQUEST", f"schema is not a valid JSON Schema: {exc.message}"
            ) from exc
        max_wait = auth.installation.manifest["spec"]["resources"]["maxInputWaitSeconds"]
        remaining = max_wait - store.input_wait_used(run)
        if body.timeoutSeconds > remaining:
            raise ApiError(
                422,
                "INPUT_WAIT_BUDGET_EXCEEDED",
                f"timeoutSeconds {body.timeoutSeconds} exceeds the remaining budget of {int(remaining)}s",
            )
        request = InputRequest(
            id=new_id("in"),
            run_id=run.id,
            agent_id=run.agent_id,
            key=body.key,
            title=body.title,
            prompt=body.prompt,
            schema=body.answer_schema,
            choices=[c.model_dump() for c in body.choices] if body.choices else None,
            preview=body.preview,
            consequence=body.consequence,
            timeout_seconds=body.timeoutSeconds,
            content_hash=digest,
            created_at=utcnow(),
            deadline=services.input_deadline(body.timeoutSeconds),
        )
        store.inputs[request.id] = request
        store.input_keys[(run.id, request.key)] = request.id
        store.append_event(
            run, "input.requested", {"inputRequestId": request.id, "key": request.key, "title": request.title}
        )
        if run.state == "RUNNING":
            store.transition(run, "WAITING_INPUT", f"waiting for {request.key}")
        services.schedule_auto_answer(store, request)
        await store.notify()
        return input_broker_view(request)

    return await auth.store.faults.run("broker.input.create", operation)


@router.get("/input-requests/{key}")
async def get_input(
    key: str, waitSeconds: int = Query(0, ge=0, le=25), auth: RunAuth = Depends(run_auth)
) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        require(auth, "input.ask", "broker.input.get")
        store = auth.store
        request_id = store.input_keys.get((auth.run.id, key))
        if request_id is None:
            raise ApiError(404, "NOT_FOUND", f"no input request with key {key}")
        request = store.inputs[request_id]
        if services.expire_if_due(store, request):
            await store.notify()
        if request.state == "pending" and waitSeconds > 0:
            until_deadline = (request.deadline - utcnow()).total_seconds()
            await store.wait_until(
                lambda: request.state != "pending", min(waitSeconds, max(0.0, until_deadline))
            )
            if services.expire_if_due(store, request):
                await store.notify()
        return input_broker_view(request)

    return await auth.store.faults.run("broker.input.get", operation)
