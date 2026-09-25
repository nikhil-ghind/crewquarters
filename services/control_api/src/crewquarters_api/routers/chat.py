"""Opt-in local chat (PLAN.md sections 9, 13.9). Owner: Akshay Sunil Navani (Person 2).

Enabling a session takes a model lease through the gateway (the model loads if it is
cold); disabling releases it so the idle timer can unload the model. Messages stream
from the gateway over SSE and are stored with their status. Knowledge-grounded answers
need the knowledge service (Nikhil Sajan Khaneja, Person 3); until it exists a session
with a knowledge base is refused rather than silently answered without sources.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_api import idempotency, schemas
from crewquarters_api.deps import AppState, AuthContext, app_state, current_auth, get_db, request_id
from crewquarters_api.pagination import clamp_limit, decode_cursor, encode_cursor
from crewquarters_shared import audit
from crewquarters_shared.db.models_gateway import ChatMessage, ChatSession
from crewquarters_shared.errors import PlatformError, conflict, not_found
from crewquarters_shared.timeutil import utcnow

router = APIRouter(tags=["chat"])

ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": schemas.ErrorResponse},
    409: {"model": schemas.ErrorResponse},
    422: {"model": schemas.ErrorResponse},
}
SYSTEM_PROMPT = (
    "You are the local assistant on this Crewquarters device. Answer clearly and "
    "concisely. If you are unsure, say so instead of guessing."
)
HISTORY_MESSAGES = 20
HISTORY_CHARS = 24_000


def _session_out(session: ChatSession) -> dict[str, Any]:
    return {
        "id": session.id,
        "title": session.title,
        "model_profile": session.model_profile,
        "knowledge_base_id": session.knowledge_base_id,
        "retrieval_mode": session.retrieval_mode,
        "enabled": session.enabled,
        "holds_model_lease": session.active_lease_id is not None,
        "local": True,
        "version": session.version,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "last_message_at": session.last_message_at,
    }


async def _owned(
    db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID, lock: bool = False
) -> ChatSession:
    stmt = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == user_id,
        ChatSession.deleted_at.is_(None),
    )
    if lock:
        stmt = stmt.with_for_update()
    session = await db.scalar(stmt)
    if session is None:
        raise not_found("Chat session", session_id)
    return session


@router.post(
    "/chat/sessions",
    response_model=schemas.ChatSessionOut,
    status_code=201,
    responses=ERRORS,
    summary="Create a chat session (disabled until enabled)",
)
async def create_session(
    body: schemas.ChatSessionCreateIn,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    idem = await idempotency.begin(
        db, request, auth.user.id, body.model_dump(by_alias=True, mode="json")
    )
    if idem.replay:
        return idem.replay
    if not body.model_profile.startswith("local."):
        raise PlatformError("PERMISSION_DENIED", "Chat is local-only.", 403)
    if await state.models.get_model(body.model_profile) is None:
        raise not_found("Model", body.model_profile)
    if body.knowledge_base_id is not None:
        raise conflict(
            "KNOWLEDGE_UNAVAILABLE",
            "Knowledge-grounded chat needs the knowledge service, which is not installed yet.",
        )
    session = ChatSession(
        user_id=auth.user.id,
        title=body.title or "New chat",
        model_profile=body.model_profile,
        retrieval_mode=body.retrieval_mode,
        enabled=False,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return await idempotency.finish(
        db, idem, 201, schemas.ChatSessionOut.model_validate(_session_out(session))
    )


@router.get(
    "/chat/sessions",
    response_model=schemas.Page[schemas.ChatSessionOut],
    summary="List chat sessions",
)
async def list_sessions(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
) -> schemas.Page[schemas.ChatSessionOut]:
    limit = clamp_limit(limit)
    stmt = (
        select(ChatSession)
        .where(ChatSession.user_id == auth.user.id, ChatSession.deleted_at.is_(None))
        .order_by(ChatSession.id.desc())
        .limit(limit + 1)
    )
    after = decode_cursor(cursor)
    if after:
        stmt = stmt.where(ChatSession.id < after)
    rows = list((await db.scalars(stmt)).all())
    return schemas.Page(
        items=[schemas.ChatSessionOut.model_validate(_session_out(s)) for s in rows[:limit]],
        next_cursor=encode_cursor(rows[limit - 1].id) if len(rows) > limit else None,
    )


@router.get(
    "/chat/sessions/{session_id}",
    response_model=schemas.ChatSessionDetailOut,
    responses=ERRORS,
    summary="Get a chat session with its recent messages",
)
async def get_session(
    session_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
) -> schemas.ChatSessionDetailOut:
    session = await _owned(db, session_id, auth.user.id)
    messages = list(
        (
            await db.scalars(
                select(ChatMessage)
                .where(ChatMessage.session_id == session.id)
                .order_by(ChatMessage.id.desc())
                .limit(200)
            )
        ).all()
    )
    return schemas.ChatSessionDetailOut.model_validate(
        {
            **_session_out(session),
            "messages": [schemas.ChatMessageOut.model_validate(m) for m in reversed(messages)],
        }
    )


async def _toggle(
    session_id: uuid.UUID,
    enable: bool,
    request: Request,
    auth: AuthContext,
    state: AppState,
    db: AsyncSession,
) -> Response:
    idem = await idempotency.begin(
        db, request, auth.user.id, {"id": str(session_id), "enable": enable}
    )
    if idem.replay:
        return idem.replay
    session = await _owned(db, session_id, auth.user.id, lock=True)
    gateway = _gateway(state)
    if enable and not session.enabled:
        lease = await gateway.acquire_chat_lease(
            session.model_profile, str(session.id), session.title
        )
        session.enabled = True
        session.active_lease_id = uuid.UUID(lease["id"])
    elif not enable and session.enabled:
        await gateway.release_chat_lease(str(session.id))
        session.enabled = False
        session.active_lease_id = None
    session.version += 1
    audit.record(
        db,
        action="chat.enabled" if enable else "chat.disabled",
        actor_type="user",
        actor_id=auth.user.id,
        target_type="chat_session",
        target_id=session.id,
        request_id=request_id(request),
        metadata={"model": session.model_profile},
    )
    await db.flush()
    await db.refresh(session)
    return await idempotency.finish(
        db, idem, 200, schemas.ChatSessionOut.model_validate(_session_out(session))
    )


@router.post(
    "/chat/sessions/{session_id}/enable",
    response_model=schemas.ChatSessionOut,
    responses=ERRORS,
    summary="Enable local chat: leases the model (it loads if cold)",
)
async def enable_session(
    session_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await _toggle(session_id, True, request, auth, state, db)


@router.post(
    "/chat/sessions/{session_id}/disable",
    response_model=schemas.ChatSessionOut,
    responses=ERRORS,
    summary="Disable chat: releases the model lease; history is kept",
)
async def disable_session(
    session_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await _toggle(session_id, False, request, auth, state, db)


@router.delete(
    "/chat/sessions/{session_id}",
    status_code=204,
    responses=ERRORS,
    summary="Delete a chat session",
)
async def delete_session(
    session_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    idem = await idempotency.begin(db, request, auth.user.id, {"id": str(session_id)})
    if idem.replay:
        return idem.replay
    session = await _owned(db, session_id, auth.user.id, lock=True)
    if session.enabled:
        await _gateway(state).release_chat_lease(str(session.id))
    session.enabled = False
    session.active_lease_id = None
    session.deleted_at = utcnow()
    return await idempotency.finish(db, idem, 204, None)


@router.post(
    "/chat/sessions/{session_id}/messages",
    responses={
        200: {
            "description": "Server-sent events: 'message' (stored user message and the "
            "assistant message id), 'delta' ({text}), 'done' (the completed assistant "
            "message), or 'error'. Closing the connection stops generation.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        **ERRORS,
    },
    summary="Send a message; the reply streams back (SSE)",
)
async def send_message(
    session_id: uuid.UUID,
    body: schemas.ChatMessageIn,
    auth: AuthContext = Depends(current_auth),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    session = await _owned(db, session_id, auth.user.id, lock=True)
    if not session.enabled:
        raise conflict("CHAT_DISABLED", "Enable chat before sending messages.")
    history = list(
        (
            await db.scalars(
                select(ChatMessage)
                .where(ChatMessage.session_id == session.id, ChatMessage.status == "complete")
                .order_by(ChatMessage.id.desc())
                .limit(HISTORY_MESSAGES)
            )
        ).all()
    )
    user_message = ChatMessage(
        session_id=session.id, role="user", content=body.content, status="complete"
    )
    assistant = ChatMessage(
        session_id=session.id,
        role="assistant",
        content="",
        status="streaming",
        model=session.model_profile,
        provider="local",
    )
    db.add_all([user_message])
    await db.flush()
    db.add(assistant)
    session.last_message_at = utcnow()
    await db.commit()
    await db.refresh(user_message)
    await db.refresh(assistant)

    messages: list[dict[str, str]] = []
    budget = HISTORY_CHARS
    for message in history:  # newest first; keep what fits
        budget -= len(message.content)
        if budget < 0:
            break
        messages.append({"role": message.role, "content": message.content})
    request_body = {
        "profile": session.model_profile,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            *reversed(messages),
            {"role": "user", "content": body.content},
        ],
        "maxOutputTokens": 1024,
        "holder": {"type": "chat", "id": str(session.id), "label": session.title},
    }
    gateway = _gateway(state)
    user_out = schemas.ChatMessageOut.model_validate(user_message).model_dump(
        by_alias=True, mode="json"
    )
    assistant_id = assistant.id

    async def stream() -> AsyncIterator[str]:
        parts: list[str] = []
        status, usage, final_text = "failed", None, None
        error: dict[str, Any] | None = None
        yield _sse("message", {"userMessage": user_out, "assistantMessageId": str(assistant_id)})
        try:
            async for event in gateway.chat_stream(request_body):
                if event["type"] == "delta":
                    parts.append(event["text"])
                    yield _sse("delta", {"text": event["text"]})
                elif event["type"] == "done":
                    status = "complete"
                    usage = event["response"].get("usage")
                    final_text = event["response"].get("text")
                elif event["type"] == "error":
                    error = event["error"]
        except PlatformError as exc:
            error = {"code": exc.code, "message": exc.message}
        except BaseException:
            status = "stopped"  # client disconnected (Stop) or shutdown
            await _finish(state, assistant_id, "".join(parts), status, usage)
            raise
        text = final_text if final_text is not None else "".join(parts)
        saved = await _finish(state, assistant_id, text, status, usage)
        if error is not None:
            yield _sse("error", error)
        else:
            yield _sse("done", saved)

    await db.close()
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def _finish(
    state: AppState, message_id: uuid.UUID, text: str, status: str, usage: dict[str, Any] | None
) -> dict[str, Any]:
    async with state.sessions() as db, db.begin():
        message = await db.get(ChatMessage, message_id, with_for_update=True)
        assert message is not None
        message.content = text
        message.status = status
        message.usage = usage
        await db.flush()
        await db.refresh(message)
        return schemas.ChatMessageOut.model_validate(message).model_dump(by_alias=True, mode="json")


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _gateway(state: AppState) -> Any:
    gateway = state.models
    if not hasattr(gateway, "acquire_chat_lease"):
        raise PlatformError(
            "MODEL_GATEWAY_UNAVAILABLE",
            "Chat needs the model gateway (CQ_MODEL_GATEWAY_ADAPTER=http).",
            503,
        )
    return gateway
