"""Owner bootstrap and session authentication (PLAN.md section 10.1)."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_api import schemas, security
from crewquarters_api.deps import (
    AppState,
    AuthContext,
    app_state,
    client_ip,
    current_auth,
    get_db,
    origin_guard,
    request_id,
)
from crewquarters_shared import audit
from crewquarters_shared.db.models import Session, Setting, User
from crewquarters_shared.errors import PlatformError
from crewquarters_shared.timeutil import utcnow

router = APIRouter(tags=["auth"])

BOOTSTRAP_SETTING = "bootstrap.token"


def _limit(state: AppState, request: Request, bucket: str) -> None:
    key = f"{bucket}:{client_ip(request)}"
    if not state.auth_limiter.allow(key):
        raise PlatformError(
            "RATE_LIMITED",
            "Too many attempts. Wait and try again.",
            429,
            {"retryAfterSeconds": state.auth_limiter.retry_after(key)},
        )


def user_out(user: User) -> schemas.UserOut:
    return schemas.UserOut.model_validate(user)


async def _start_session(
    db: AsyncSession, state: AppState, request: Request, response: Response, user: User
) -> schemas.SessionOut:
    token = security.new_token()
    token_hash = security.hash_token(token)
    now = utcnow()
    settings = state.settings
    session = Session(
        token_hash=token_hash,
        user_id=user.id,
        expires_at=now + timedelta(seconds=settings.session_absolute_seconds),
        idle_expires_at=now + timedelta(seconds=settings.session_idle_seconds),
        last_seen_at=now,
        user_agent=(request.headers.get("user-agent") or "")[:256],
        ip_address=client_ip(request),
    )
    db.add(session)
    csrf = security.csrf_token(settings.secret_key.get_secret_value(), token_hash)
    _set_cookies(response, state, token, csrf, session.expires_at)
    return schemas.SessionOut(
        user=user_out(user),
        csrf_token=csrf,
        expires_at=session.expires_at,
        idle_expires_at=session.idle_expires_at,
    )


def _set_cookies(
    response: Response, state: AppState, token: str, csrf: str, expires: datetime
) -> None:
    max_age = int((expires - utcnow()).total_seconds())
    secure = state.settings.cookie_secure
    response.set_cookie(
        security.SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        security.CSRF_COOKIE,
        csrf,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


@router.post(
    "/bootstrap",
    response_model=schemas.SessionOut,
    status_code=201,
    summary="Create the device owner with the one-time bootstrap token",
    responses={409: {"model": schemas.ErrorResponse}, 429: {"model": schemas.ErrorResponse}},
    dependencies=[Depends(origin_guard)],
)
async def bootstrap(
    body: schemas.BootstrapIn,
    request: Request,
    response: Response,
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.SessionOut:
    _limit(state, request, "bootstrap")
    token_row = await db.get(Setting, BOOTSTRAP_SETTING, with_for_update=True)
    users = await db.scalar(select(func.count()).select_from(User))
    if users:
        raise PlatformError("ALREADY_BOOTSTRAPPED", "The owner account already exists.", 409)
    valid = (
        token_row is not None
        and datetime.fromisoformat(token_row.value["expiresAt"]) > utcnow()
        and security.constant_time_equals(token_row.value["hash"], security.hash_token(body.token))
    )
    if not valid:
        audit.record(
            db,
            action="auth.bootstrap",
            actor_type="anonymous",
            outcome="denied",
            request_id=request_id(request),
            ip_address=client_ip(request),
        )
        await db.commit()
        raise PlatformError("INVALID_BOOTSTRAP_TOKEN", "The setup code is invalid or expired.", 403)
    user = User(
        username=body.username,
        username_normalized=body.username.strip().lower(),
        email=body.email,
        password_hash=security.hash_password(body.password),
        role="owner",
    )
    db.add(user)
    await db.delete(token_row)
    await db.flush()
    out = await _start_session(db, state, request, response, user)
    audit.record(
        db,
        action="auth.bootstrap",
        actor_type="user",
        actor_id=user.id,
        target_type="user",
        target_id=user.id,
        request_id=request_id(request),
        ip_address=client_ip(request),
    )
    await db.commit()
    return out


@router.post(
    "/sessions",
    response_model=schemas.SessionOut,
    status_code=201,
    summary="Sign in",
    responses={401: {"model": schemas.ErrorResponse}, 429: {"model": schemas.ErrorResponse}},
    dependencies=[Depends(origin_guard)],
)
async def login(
    body: schemas.LoginIn,
    request: Request,
    response: Response,
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> schemas.SessionOut:
    _limit(state, request, "login")
    user = await db.scalar(
        select(User).where(User.username_normalized == body.username.strip().lower())
    )
    ok = security.verify_password(user.password_hash if user else None, body.password)
    if not ok or user is None or user.disabled_at is not None:
        audit.record(
            db,
            action="auth.login",
            actor_type="anonymous",
            outcome="denied",
            request_id=request_id(request),
            ip_address=client_ip(request),
            metadata={"username": body.username[:64]},
        )
        await db.commit()
        raise PlatformError("INVALID_CREDENTIALS", "The username or password is incorrect.", 401)
    if security.needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(body.password)
    out = await _start_session(db, state, request, response, user)
    audit.record(
        db,
        action="auth.login",
        actor_type="user",
        actor_id=user.id,
        request_id=request_id(request),
        ip_address=client_ip(request),
    )
    await db.commit()
    return out


@router.delete("/sessions/current", status_code=204, summary="Sign out")
async def logout(
    request: Request,
    response: Response,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    session = await db.get(Session, auth.session.id)
    if session is not None:
        session.revoked_at = utcnow()
    audit.record(
        db,
        action="auth.logout",
        actor_type="user",
        actor_id=auth.user.id,
        request_id=request_id(request),
        ip_address=client_ip(request),
    )
    await db.commit()
    response = Response(status_code=204)
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    response.delete_cookie(security.CSRF_COOKIE, path="/")
    return response


@router.get("/me", response_model=schemas.SessionOut, summary="Current user and CSRF token")
async def me(
    auth: AuthContext = Depends(current_auth), state: AppState = Depends(app_state)
) -> schemas.SessionOut:
    return schemas.SessionOut(
        user=user_out(auth.user),
        csrf_token=security.csrf_token(
            state.settings.secret_key.get_secret_value(), auth.token_hash
        ),
        expires_at=auth.session.expires_at,
        idle_expires_at=auth.session.idle_expires_at,
    )
