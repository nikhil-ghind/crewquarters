"""Broker input-request routes (``ctx.input.ask``)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from crewquarters_fake import services
from crewquarters_fake.broker.auth import RunAuth, require, run_auth
from crewquarters_fake.errors import ApiError
from crewquarters_fake.store import utcnow
from crewquarters_fake.views import input_broker_view

router = APIRouter()


class InputCreateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=4000)
    answer_schema: dict[str, Any] = Field(alias="schema")
    timeoutSeconds: int = Field(ge=1, le=86400)
    preview: dict[str, Any] | None = None


@router.post("/input-requests")
async def create_input(body: InputCreateIn, auth: RunAuth = Depends(run_auth)) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        require(auth, "user_input", "broker.input.create")
        request = services.ask(
            auth.store,
            auth.run,
            key=body.key,
            title=body.title,
            prompt=body.prompt,
            schema=body.answer_schema,
            timeout_seconds=body.timeoutSeconds,
            preview=body.preview,
        )
        await auth.store.notify()
        return input_broker_view(request)

    return await auth.store.faults.run("broker.input.create", operation)


@router.get("/input-requests/{input_request_id}")
async def get_input(
    input_request_id: str,
    wait: float = Query(0, ge=0, le=30),
    auth: RunAuth = Depends(run_auth),
) -> dict[str, Any]:
    async def operation() -> dict[str, Any]:
        require(auth, "user_input", "broker.input.get")
        store = auth.store
        request = store.inputs.get(input_request_id)
        if request is None or request.run_id != auth.run.id:
            raise ApiError(404, "NOT_FOUND", "Input request not found.", {"id": input_request_id})
        if services.expire_if_due(store, request):
            await store.notify()
        if request.state == "pending" and wait > 0:
            until_deadline = (request.deadline - utcnow()).total_seconds()
            await store.wait_until(
                lambda: request.state != "pending", min(wait, max(0.0, until_deadline))
            )
            if services.expire_if_due(store, request):
                await store.notify()
        return input_broker_view(request)

    return await auth.store.faults.run("broker.input.get", operation)
