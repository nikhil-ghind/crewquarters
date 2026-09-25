"""Internal knowledge API (``/internal/v1``, service token only).

Callers: the control API (owner knowledge pages and uploads), the capability broker
(agent ``knowledge.search:config``), and the model gateway (RAG chat). Each caller has
already authorized the knowledge base; this service enforces the KB scope of every query.
"""

from __future__ import annotations

import hmac
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_knowledge import service
from crewquarters_knowledge.config import KnowledgeSettings
from crewquarters_knowledge.embeddings import Embedder
from crewquarters_knowledge.metrics import KnowledgeMetrics
from crewquarters_knowledge.models import Document, KnowledgeBase
from crewquarters_shared.errors import PlatformError, not_found
from crewquarters_shared.metrics import CONTENT_TYPE


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class KnowledgeBaseIn(ApiModel):
    owner_id: uuid.UUID
    name: str = Field(min_length=1, max_length=100)


class QueryIn(ApiModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(8, ge=1, le=50)
    max_context_tokens: int = Field(5000, ge=100, le=20000)
    filters: dict[str, list[uuid.UUID]] = Field(default_factory=dict)


@dataclass
class KnowledgeState:
    settings: KnowledgeSettings
    sessions: async_sessionmaker[AsyncSession]
    embedder: Embedder
    metrics: KnowledgeMetrics


def state_of(request: Request) -> KnowledgeState:
    state: KnowledgeState = request.app.state.knowledge
    return state


async def internal_auth(request: Request, state: KnowledgeState = Depends(state_of)) -> None:
    expected = state.settings.internal_service_token.get_secret_value()
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(value.encode(), expected.encode()):
        raise PlatformError("UNAUTHENTICATED", "Service credential required.", 401)


router = APIRouter(prefix="/internal/v1", dependencies=[Depends(internal_auth)])


@router.post("/knowledge-bases", status_code=201, summary="Create a knowledge base")
async def create_kb(body: KnowledgeBaseIn, state: KnowledgeState = Depends(state_of)) -> Any:
    async with state.sessions() as db:
        kb = await service.create_kb(db, state.embedder, body.owner_id, body.name)
        return service.kb_view(kb)


@router.get("/knowledge-bases", summary="List knowledge bases")
async def list_kbs(state: KnowledgeState = Depends(state_of)) -> Any:
    async with state.sessions() as db:
        kbs = (await db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.created_at))).all()
        return [service.kb_view(kb) for kb in kbs]


@router.get("/knowledge-bases/{kb_id}", summary="One knowledge base")
async def get_kb(kb_id: uuid.UUID, state: KnowledgeState = Depends(state_of)) -> Any:
    async with state.sessions() as db:
        return service.kb_view(await service.get_kb(db, kb_id))


@router.delete("/knowledge-bases/{kb_id}", status_code=204, summary="Delete a KB; files are purged")
async def delete_kb(kb_id: uuid.UUID, state: KnowledgeState = Depends(state_of)) -> Response:
    async with state.sessions() as db:
        await service.delete_kb(db, kb_id)
    return Response(status_code=204)


@router.post(
    "/knowledge-bases/{kb_id}/documents", status_code=202, summary="Upload; indexing is async"
)
async def upload(
    kb_id: uuid.UUID, file: UploadFile = File(...), state: KnowledgeState = Depends(state_of)
) -> Any:
    async with state.sessions() as db:
        return service.document_view(await service.upload(db, state.settings, kb_id, file))


@router.get("/knowledge-bases/{kb_id}/documents", summary="Documents and their states")
async def list_documents(kb_id: uuid.UUID, state: KnowledgeState = Depends(state_of)) -> Any:
    async with state.sessions() as db:
        await service.get_kb(db, kb_id)
        docs = (
            await db.scalars(
                select(Document).where(Document.kb_id == kb_id).order_by(Document.created_at)
            )
        ).all()
        return [service.document_view(d) for d in docs]


@router.get("/documents/{document_id}", summary="One document")
async def get_document(document_id: uuid.UUID, state: KnowledgeState = Depends(state_of)) -> Any:
    async with state.sessions() as db:
        doc = await db.get(Document, document_id)
        if doc is None:
            raise not_found("Document", document_id)
        return service.document_view(doc)


@router.delete("/documents/{document_id}", status_code=204, summary="Delete a document")
async def delete_document(
    document_id: uuid.UUID, state: KnowledgeState = Depends(state_of)
) -> Response:
    async with state.sessions() as db:
        await service.delete_document(db, document_id)
    return Response(status_code=204)


@router.post("/documents/{document_id}/reindex", status_code=202, summary="Re-index a document")
async def reindex(document_id: uuid.UUID, state: KnowledgeState = Depends(state_of)) -> Any:
    async with state.sessions() as db:
        return service.document_view(await service.reindex_document(db, document_id))


@router.post("/knowledge-bases/{kb_id}/query", summary="Cited retrieval within one KB")
async def query(kb_id: uuid.UUID, body: QueryIn, state: KnowledgeState = Depends(state_of)) -> Any:
    started = time.perf_counter()
    async with state.sessions() as db:
        result = await service.query(
            db,
            state.embedder,
            kb_id,
            body.query,
            body.top_k,
            body.max_context_tokens,
            body.filters.get("documentIds"),
        )
    state.metrics.retrieval.observe(time.perf_counter() - started)
    return result


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
async def metrics(state: KnowledgeState = Depends(state_of)) -> PlainTextResponse:
    async with state.sessions() as db:
        await state.metrics.collect_database(db)
    return PlainTextResponse(state.metrics.render(), media_type=CONTENT_TYPE)
