"""Broker route for starting another agent."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from crewquarters_fake import services
from crewquarters_fake.broker.audit import audited
from crewquarters_fake.broker.auth import RunAuth, require, run_auth

router = APIRouter()


class StartIn(BaseModel):
    agentId: str = Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")
    startKey: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,200}$")
    input: dict[str, Any] | None = None


@router.post("/agents/start")
async def start(
    body: StartIn, request: Request, auth: RunAuth = Depends(run_auth)
) -> dict[str, Any]:
    require(auth, f"agents.start:{body.agentId}", "broker.agents.start")

    async def call() -> dict[str, Any]:
        child, created = services.start_agent_run(
            auth.store,
            auth.run,
            agent_id=body.agentId,
            start_key=body.startKey,
            trigger_input=body.input,
        )
        return {
            "runId": child.id,
            "agentId": child.agent_id,
            "installationId": child.installation_id,
            "state": child.state,
            "created": created,
        }

    return await audited(auth, request, "agents", "broker.agents.start", call)
