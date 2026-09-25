"""Connection management (PLAN.md sections 5.1, 10.3, 13.10): thin, owner-only proxies to
the capability broker's ``/internal/v1`` connection API, plus provider-key tests through
the model gateway.

The control API never sees a decrypted credential: Twilio tokens and API keys pass through
to the broker, which encrypts them; only the gateway decrypts OpenAI/Anthropic keys
(PLAN.md section 10.2). Responses never contain secret values.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from crewquarters_api import idempotency, schemas
from crewquarters_api.deps import AppState, AuthContext, app_state, get_db, require_owner
from crewquarters_api.pagination import page_in_memory

router = APIRouter(tags=["connections"])

ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": schemas.ErrorResponse},
    409: {"model": schemas.ErrorResponse},
    422: {"model": schemas.ErrorResponse},
    502: {"model": schemas.ErrorResponse},
    503: {"model": schemas.ErrorResponse},
}
BINDING_COOKIE = "cq_oauth_binding"
BINDING_COOKIE_PATH = "/api/v1/connections/google"
BINDING_MAX_AGE = 600


def _secret_digest(state: AppState, value: str) -> str:
    """A keyed digest of a secret for the idempotency request hash: a retried save with the
    same key and secret replays, a different secret is a different request, and no
    unkeyed hash of the secret is stored."""
    key = state.settings.secret_key.get_secret_value().encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


async def _proxied(
    request: Request,
    auth: AuthContext,
    db: AsyncSession,
    state: AppState,
    fingerprint: dict[str, Any],
    call: Callable[[], Awaitable[Any]],
    status_code: int = 200,
    model: type[schemas.ApiModel] | None = None,
) -> Response:
    """Run one broker/gateway call under HTTP idempotency; refresh cached status."""
    idem = await idempotency.begin(db, request, auth.user.id, fingerprint)
    if idem.replay:
        return idem.replay
    result = await call()
    state.broker.invalidate()
    if status_code == 204:
        return await idempotency.finish(db, idem, 204, None)
    body = model.model_validate(result) if model is not None else result
    return await idempotency.finish(db, idem, status_code, body)


# --- Google ---------------------------------------------------------------------------------


@router.post(
    "/connections/google/start",
    response_model=schemas.GoogleStartOut,
    responses=ERRORS,
    summary="Begin Google consent: returns the authorization URL and sets the binding cookie",
)
async def google_start(
    body: schemas.GoogleStartIn,
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
) -> JSONResponse:
    """Not replayable: every call starts a new single-use consent with a new browser binding,
    so an ``Idempotency-Key`` is ignored here."""
    started = await state.broker.request(
        "POST",
        "/connections/google/start",
        json={"userId": str(auth.user.id), "capabilities": list(dict.fromkeys(body.capabilities))},
    )
    out = schemas.GoogleStartOut(authorization_url=started["authorizationUrl"])
    response = JSONResponse(out.model_dump(by_alias=True))
    response.set_cookie(
        BINDING_COOKIE,
        started["browserBinding"],
        max_age=BINDING_MAX_AGE,
        path=BINDING_COOKIE_PATH,
        httponly=True,
        secure=state.settings.cookie_secure or request.url.scheme == "https",
        samesite="lax",
    )
    return response


@router.post(
    "/connections/google/test",
    response_model=schemas.ConnectionOut,
    responses=ERRORS,
    summary="Refresh Google access now; an expired grant becomes NEEDS_ATTENTION",
)
async def google_test(
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    async def call() -> dict[str, Any]:
        result = await state.broker.request("POST", "/connections/google/test")
        return {"provider": "google", "displayName": "Google", **result}

    return await _proxied(
        request, auth, db, state, {"test": "google"}, call, model=schemas.ConnectionOut
    )


@router.delete(
    "/connections/google",
    status_code=204,
    responses=ERRORS,
    summary="Disconnect Google: revoke at Google, then delete the connection and its secret",
)
async def google_disconnect(
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    call = partial(
        state.broker.request, "DELETE", "/connections/google", params={"userId": str(auth.user.id)}
    )
    return await _proxied(request, auth, db, state, {"delete": "google"}, call, 204)


# --- Twilio ---------------------------------------------------------------------------------


@router.put(
    "/connections/twilio",
    response_model=schemas.ConnectionOut,
    responses=ERRORS,
    summary="Save (or replace) Twilio credentials, then validate them without calling",
)
async def twilio_save(
    body: schemas.TwilioCredentialsIn,
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    async def call() -> dict[str, Any]:
        result = await state.broker.request(
            "PUT",
            "/connections/twilio",
            json={
                "userId": str(auth.user.id),
                "accountSid": body.account_sid,
                "authToken": body.auth_token,
                "fromNumber": body.from_number,
            },
        )
        return {"provider": "twilio", "displayName": "Twilio", **result}

    fingerprint = {
        "accountSid": body.account_sid,
        "fromNumber": body.from_number,
        "authToken": _secret_digest(state, body.auth_token),
    }
    return await _proxied(request, auth, db, state, fingerprint, call, model=schemas.ConnectionOut)


@router.post(
    "/connections/twilio/test",
    response_model=schemas.ConnectionOut,
    responses=ERRORS,
    summary="Validate Twilio credentials without placing a call",
)
async def twilio_test(
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    async def call() -> dict[str, Any]:
        result = await state.broker.request("POST", "/connections/twilio/test")
        return {"provider": "twilio", "displayName": "Twilio", **result}

    return await _proxied(
        request, auth, db, state, {"test": "twilio"}, call, model=schemas.ConnectionOut
    )


@router.post(
    "/connections/twilio/test-call",
    response_model=schemas.TwilioTestCallOut,
    responses={**ERRORS, 429: {"model": schemas.ErrorResponse}},
    summary="Place one live test call (the owner must confirm; at most one a minute)",
)
async def twilio_test_call(
    body: schemas.TwilioTestCallIn,
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """A retry with the same ``Idempotency-Key`` replays the first result and never dials
    twice."""
    call = partial(
        state.broker.request,
        "POST",
        "/connections/twilio/test-call",
        json={"userId": str(auth.user.id), "to": body.to, "confirm": body.confirm},
    )
    fingerprint = {"to": _secret_digest(state, body.to), "confirm": body.confirm}
    return await _proxied(
        request, auth, db, state, fingerprint, call, model=schemas.TwilioTestCallOut
    )


@router.delete(
    "/connections/twilio",
    status_code=204,
    responses=ERRORS,
    summary="Delete the Twilio credentials",
)
async def twilio_delete(
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    call = partial(
        state.broker.request, "DELETE", "/connections/twilio", params={"userId": str(auth.user.id)}
    )
    return await _proxied(request, auth, db, state, {"delete": "twilio"}, call, 204)


# --- OpenAI / Anthropic provider keys --------------------------------------------------------


@router.get(
    "/provider-profiles",
    response_model=schemas.Page[schemas.ProviderProfileOut],
    responses=ERRORS,
    summary="Cloud provider profiles (API keys are never returned)",
)
async def list_provider_profiles(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    _: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
) -> schemas.Page[schemas.ProviderProfileOut]:
    profiles = await state.broker.request("GET", "/provider-profiles")
    page, nxt = page_in_memory(profiles, lambda p: str(p["id"]), limit, cursor)
    return schemas.Page(
        items=[schemas.ProviderProfileOut.model_validate(p) for p in page], next_cursor=nxt
    )


@router.post(
    "/provider-profiles",
    response_model=schemas.ProviderProfileOut,
    status_code=201,
    responses=ERRORS,
    summary="Store an OpenAI or Anthropic API key (encrypted by the broker)",
)
async def create_provider_profile(
    body: schemas.ProviderProfileCreateIn,
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    payload = body.model_dump(by_alias=True, mode="json")
    call = partial(
        state.broker.request,
        "POST",
        "/provider-profiles",
        json={"userId": str(auth.user.id), **payload},
    )
    fingerprint = {**payload, "apiKey": _secret_digest(state, body.api_key)}
    return await _proxied(
        request, auth, db, state, fingerprint, call, 201, model=schemas.ProviderProfileOut
    )


@router.delete(
    "/provider-profiles/{profile_id}",
    status_code=204,
    responses=ERRORS,
    summary="Delete a provider profile and its key",
)
async def delete_provider_profile(
    profile_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    call = partial(
        state.broker.request,
        "DELETE",
        f"/provider-profiles/{profile_id}",
        params={"userId": str(auth.user.id)},
    )
    return await _proxied(request, auth, db, state, {"id": str(profile_id)}, call, 204)


@router.post(
    "/provider-profiles/{profile_id}/test",
    response_model=schemas.ProviderProfileTestOut,
    responses=ERRORS,
    summary="Test a provider key with the smallest useful request (content leaves the device)",
)
async def test_provider_profile(
    profile_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_owner),
    state: AppState = Depends(app_state),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The model gateway decrypts the key and calls the provider; the control API never
    holds the key."""
    call = partial(state.gateway_admin.request, "POST", f"/provider-profiles/{profile_id}/test")
    return await _proxied(
        request,
        auth,
        db,
        state,
        {"test": str(profile_id)},
        call,
        model=schemas.ProviderProfileTestOut,
    )
