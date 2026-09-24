"""FastAPI dependencies: database sessions, authentication, CSRF/origin checks,
internal service authentication, and shared application state."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_api import security
from crewquarters_shared.clients import ConnectionStatusClient, ModelStatusClient
from crewquarters_shared.config import Settings
from crewquarters_shared.db.models import Session, User
from crewquarters_shared.errors import PlatformError
from crewquarters_shared.metrics import ApiMetrics
from crewquarters_shared.runtime import RuntimeAdapter
from crewquarters_shared.timeutil import utcnow

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SESSION_TOUCH_SECONDS = 60


@dataclass
class AppState:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    models: ModelStatusClient
    connections: ConnectionStatusClient
    auth_limiter: security.RateLimiter
    metrics: ApiMetrics
    runtime: RuntimeAdapter | None = None


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: Session
    token_hash: str


def app_state(request: Request) -> AppState:
    state: AppState = request.app.state.cq
    return state


async def get_db(state: AppState = Depends(app_state)) -> AsyncIterator[AsyncSession]:
    async with state.sessions() as session:
        yield session


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def check_origin(request: Request, settings: Settings) -> None:
    """Reject cross-origin state changes. Browsers always send Origin on POST/PATCH/DELETE."""
    if request.method not in UNSAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer", "")
        origin = "/".join(referer.split("/")[:3]) if referer else None
    if origin is None or origin.rstrip("/") not in {o.rstrip("/") for o in settings.public_origins}:
        raise PlatformError("ORIGIN_REJECTED", "Request origin is not allowed.", 403)


async def origin_guard(request: Request, state: AppState = Depends(app_state)) -> None:
    check_origin(request, state.settings)


async def current_auth(
    request: Request,
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    token = request.cookies.get(security.SESSION_COOKIE)
    if not token:
        raise PlatformError("UNAUTHENTICATED", "Sign in to continue.", 401)
    token_hash = security.hash_token(token)
    row = (
        await db.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(Session.token_hash == token_hash)
        )
    ).one_or_none()
    now = utcnow()
    if row is None:
        raise PlatformError("UNAUTHENTICATED", "Sign in to continue.", 401)
    session, user = row
    if (
        session.revoked_at is not None
        or session.expires_at <= now
        or session.idle_expires_at <= now
        or user.disabled_at is not None
    ):
        raise PlatformError("SESSION_EXPIRED", "Your session has expired. Sign in again.", 401)
    if request.method in UNSAFE_METHODS:
        check_origin(request, state.settings)
        sent = request.headers.get(security.CSRF_HEADER, "")
        expected = security.csrf_token(state.settings.secret_key.get_secret_value(), token_hash)
        if not sent or not security.constant_time_equals(sent, expected):
            raise PlatformError("CSRF_FAILED", "The request is missing a valid CSRF token.", 403)
    if (now - session.last_seen_at).total_seconds() > SESSION_TOUCH_SECONDS:
        session.last_seen_at = now
        session.idle_expires_at = min(
            session.expires_at, now + timedelta(seconds=state.settings.session_idle_seconds)
        )
        await db.commit()
    return AuthContext(user=user, session=session, token_hash=token_hash)


async def require_owner(auth: AuthContext = Depends(current_auth)) -> AuthContext:
    if auth.user.role != "owner":
        raise PlatformError("FORBIDDEN", "Only the device owner can do this.", 403)
    return auth


async def internal_auth(request: Request, state: AppState = Depends(app_state)) -> None:
    """Service-to-service authentication for ``/internal/v1`` routes."""
    header = request.headers.get("authorization", "")
    expected = state.settings.internal_service_token.get_secret_value()
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not security.constant_time_equals(value, expected):
        raise PlatformError("UNAUTHENTICATED", "Service credential required.", 401)
