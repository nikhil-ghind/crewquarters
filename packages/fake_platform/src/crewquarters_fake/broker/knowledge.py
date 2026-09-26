"""Broker knowledge-search route."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
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


@router.get("/knowledge/documents")
async def documents(
    request: Request,
    knowledgeBaseId: str = Query(max_length=64),
    pattern: str = Query("*", min_length=1, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    auth: RunAuth = Depends(run_auth),
) -> dict[str, Any]:
    require(auth, CAPABILITY, "broker.knowledge.documents")
    if knowledgeBaseId not in auth.installation.knowledge_base_ids:
        deny(auth, CAPABILITY, "broker.knowledge.documents")
    if not auth.store.knowledge.has(knowledgeBaseId):
        raise ApiError(404, "NOT_FOUND", f"knowledge base {knowledgeBaseId} not found")

    async def call() -> dict[str, Any]:
        found = auth.store.knowledge.documents(knowledgeBaseId, pattern)
        return {"documents": found[:limit], "total": len(found), "truncated": len(found) > limit}

    return await audited(auth, request, "knowledge", "broker.knowledge.documents", call)
