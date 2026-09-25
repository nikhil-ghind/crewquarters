"""Broker knowledge-search route."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from crewquarters_fake.broker.audit import audited
from crewquarters_fake.broker.auth import RunAuth, deny, require, run_auth
from crewquarters_fake.errors import ApiError

router = APIRouter()


class FiltersIn(BaseModel):
    documentIds: list[str] = []


class SearchIn(BaseModel):
    knowledgeBaseId: str
    query: str = Field(min_length=1)
    topK: int = Field(8, ge=1, le=50)
    filters: FiltersIn = FiltersIn()
    maxContextTokens: int | None = Field(None, ge=1)


CAPABILITY = "knowledge.search:config"


@router.post("/knowledge/search")
async def search(
    body: SearchIn, request: Request, auth: RunAuth = Depends(run_auth)
) -> dict[str, Any]:
    require(auth, CAPABILITY, "broker.knowledge.search")
    if body.knowledgeBaseId not in auth.installation.knowledge_base_ids:
        # Only the knowledge base selected in the installation config is searchable.
        deny(auth, CAPABILITY, "broker.knowledge.search")
    if not auth.store.knowledge.has(body.knowledgeBaseId):
        raise ApiError(404, "NOT_FOUND", f"knowledge base {body.knowledgeBaseId} not found")

    async def call() -> dict[str, Any]:
        passages = auth.store.knowledge.search(
            body.knowledgeBaseId, body.query, body.topK, body.filters.documentIds or None
        )
        if body.maxContextTokens:
            budget = body.maxContextTokens
            kept: list[dict[str, Any]] = []
            for passage in passages:
                cost = len(passage["text"].split())
                if kept and cost > budget:
                    break
                kept.append(passage)
                budget -= cost
            passages = kept
        return {"passages": passages}

    return await audited(auth, request, "knowledge", "broker.knowledge.search", call)
