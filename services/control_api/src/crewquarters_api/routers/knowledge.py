"""Knowledge bases and documents (PLAN.md sections 5.1, 9, 13.9): thin proxies to the
knowledge service's ``/internal/v1`` API, scoped to the signed-in user's own knowledge bases.

The knowledge service trusts its caller to authorize the knowledge base, so every route
here checks ownership first (``upstream.require_knowledge_base``). Someone else's knowledge
base or document is reported as ``404``.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_api import idempotency, schemas
from crewquarters_api.deps import AppState, AuthContext, app_state, current_auth, get_db
from crewquarters_api.pagination import page_in_memory
from crewquarters_api.upstream import owned_knowledge_base_ids, require_knowledge_base
from crewquarters_shared.errors import not_found

router = APIRouter(tags=["knowledge"])

ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": schemas.ErrorResponse},
    409: {"model": schemas.ErrorResponse},
    422: {"model": schemas.ErrorResponse},
    502: {"model": schemas.ErrorResponse},
    503: {"model": schemas.ErrorResponse},
}
# The upload route has its own body limit (CQ_MAX_UPLOAD_BYTES plus multipart framing)
# instead of the global CQ_MAX_BODY_BYTES; see ``main.BodySizeLimit``.
UPLOAD_PATH = re.compile(r"/api/v1/knowledge-bases/[^/]+/documents")
MULTIPART_SLACK = 64 * 1024
UPLOAD_TIMEOUT_SECONDS = 120.0


async def _document(
    state: AppState, db: AsyncSession, kb_id: uuid.UUID, document_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Any]:
    await require_knowledge_base(db, kb_id, user_id)
    doc: dict[str, Any] = await state.knowledge.request("GET", f"/documents/{document_id}")
    if doc.get("knowledgeBaseId") != str(kb_id):
        raise not_found("Document", document_id)
    return doc


@router.post(
    "/knowledge-bases",
    response_model=schemas.KnowledgeBaseOut,
    status_code=201,
    responses=ERRORS,
    summary="Create a knowledge base (the embedding profile is fixed at creation)",
)
async def create_knowledge_base(
    body: schemas.KnowledgeBaseCreateIn,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    idem = await idempotency.begin(db, request, auth.user.id, body.model_dump(by_alias=True))
    if idem.replay:
        return idem.replay
    kb = await state.knowledge.request(
        "POST", "/knowledge-bases", json={"ownerId": str(auth.user.id), "name": body.name}
    )
    return await idempotency.finish(db, idem, 201, schemas.KnowledgeBaseOut.model_validate(kb))


@router.get(
    "/knowledge-bases",
    response_model=schemas.Page[schemas.KnowledgeBaseOut],
    responses=ERRORS,
    summary="Your knowledge bases",
)
async def list_knowledge_bases(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.Page[schemas.KnowledgeBaseOut]:
    owned = await owned_knowledge_base_ids(db, auth.user.id)
    kbs = [
        kb for kb in await state.knowledge.request("GET", "/knowledge-bases") if kb["id"] in owned
    ]
    page, nxt = page_in_memory(kbs, lambda kb: str(kb["id"]), limit, cursor)
    return schemas.Page(
        items=[schemas.KnowledgeBaseOut.model_validate(kb) for kb in page], next_cursor=nxt
    )


@router.get(
    "/knowledge-bases/{kb_id}",
    response_model=schemas.KnowledgeBaseOut,
    responses=ERRORS,
    summary="One knowledge base",
)
async def get_knowledge_base(
    kb_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.KnowledgeBaseOut:
    await require_knowledge_base(db, kb_id, auth.user.id)
    return schemas.KnowledgeBaseOut.model_validate(
        await state.knowledge.request("GET", f"/knowledge-bases/{kb_id}")
    )


@router.delete(
    "/knowledge-bases/{kb_id}",
    status_code=204,
    responses=ERRORS,
    summary="Delete a knowledge base; its files are securely purged",
)
async def delete_knowledge_base(
    kb_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    idem = await idempotency.begin(db, request, auth.user.id, {"id": str(kb_id)})
    if idem.replay:
        return idem.replay
    await require_knowledge_base(db, kb_id, auth.user.id)
    await state.knowledge.request("DELETE", f"/knowledge-bases/{kb_id}")
    return await idempotency.finish(db, idem, 204, None)


@router.post(
    "/knowledge-bases/{kb_id}/documents",
    response_model=schemas.DocumentOut,
    status_code=202,
    responses={**ERRORS, 413: {"model": schemas.ErrorResponse}},
    summary="Upload one document (multipart `file`); indexing is asynchronous",
)
async def upload_document(
    kb_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(..., description=".txt, .md, .csv, text .pdf, or .docx"),
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The body limit is ``CQ_MAX_UPLOAD_BYTES`` (25 MiB) rather than the global 2 MiB; the
    knowledge service validates type, name, size, and duplicates. Not replayable: a retry
    of the same bytes returns ``409 DUPLICATE_DOCUMENT`` with the existing document id."""
    await require_knowledge_base(db, kb_id, auth.user.id)
    doc = await state.knowledge.request(
        "POST",
        f"/knowledge-bases/{kb_id}/documents",
        files={
            "file": (
                file.filename or "document",
                file.file,
                file.content_type or "application/octet-stream",
            )
        },
        read_timeout=UPLOAD_TIMEOUT_SECONDS,
    )
    out = schemas.DocumentOut.model_validate(doc)
    return Response(
        out.model_dump_json(by_alias=True), status_code=202, media_type="application/json"
    )


@router.get(
    "/knowledge-bases/{kb_id}/documents",
    response_model=schemas.Page[schemas.DocumentOut],
    responses=ERRORS,
    summary="Documents with their per-file ingestion state and errors",
)
async def list_documents(
    kb_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.Page[schemas.DocumentOut]:
    await require_knowledge_base(db, kb_id, auth.user.id)
    docs = await state.knowledge.request("GET", f"/knowledge-bases/{kb_id}/documents")
    page, nxt = page_in_memory(docs, lambda d: str(d["id"]), limit, cursor)
    return schemas.Page(
        items=[schemas.DocumentOut.model_validate(d) for d in page], next_cursor=nxt
    )


@router.get(
    "/knowledge-bases/{kb_id}/documents/{document_id}",
    response_model=schemas.DocumentOut,
    responses=ERRORS,
    summary="One document",
)
async def get_document(
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.DocumentOut:
    return schemas.DocumentOut.model_validate(
        await _document(state, db, kb_id, document_id, auth.user.id)
    )


@router.delete(
    "/knowledge-bases/{kb_id}/documents/{document_id}",
    status_code=204,
    responses=ERRORS,
    summary="Delete a document; its passages stop appearing in chat and searches",
)
async def delete_document(
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    idem = await idempotency.begin(db, request, auth.user.id, {"id": str(document_id)})
    if idem.replay:
        return idem.replay
    await _document(state, db, kb_id, document_id, auth.user.id)
    await state.knowledge.request("DELETE", f"/documents/{document_id}")
    return await idempotency.finish(db, idem, 204, None)


@router.post(
    "/knowledge-bases/{kb_id}/documents/{document_id}/reindex",
    response_model=schemas.DocumentOut,
    status_code=202,
    responses=ERRORS,
    summary="Re-index a document (for example after a failure or a profile change)",
)
async def reindex_document(
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    idem = await idempotency.begin(db, request, auth.user.id, {"id": str(document_id)})
    if idem.replay:
        return idem.replay
    await _document(state, db, kb_id, document_id, auth.user.id)
    doc = await state.knowledge.request("POST", f"/documents/{document_id}/reindex")
    return await idempotency.finish(db, idem, 202, schemas.DocumentOut.model_validate(doc))


@router.post(
    "/knowledge-bases/{kb_id}/query",
    response_model=schemas.KnowledgeQueryOut,
    responses=ERRORS,
    summary="Test retrieval: cited passages from this knowledge base only",
)
async def query_knowledge_base(
    kb_id: uuid.UUID,
    body: schemas.KnowledgeQueryIn,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.KnowledgeQueryOut:
    """Read-only, so no idempotency record. Passages are untrusted document text."""
    await require_knowledge_base(db, kb_id, auth.user.id)
    result = await state.knowledge.request(
        "POST", f"/knowledge-bases/{kb_id}/query", json=body.model_dump(by_alias=True, mode="json")
    )
    return schemas.KnowledgeQueryOut(
        knowledge_base_id=kb_id,
        passages=[schemas.PassageOut.model_validate(p) for p in result.get("passages", [])],
    )
