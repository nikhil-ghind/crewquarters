"""Public callback routes: the only broker paths the reverse proxy forwards (PLAN.md 14.3).

* ``GET /api/v1/connections/google/callback`` — OAuth redirect; returns the browser to
  the Connections page with a result code, never tokens.
* ``POST /api/v1/callbacks/twilio/{voice,gather,status}/{callId}`` — signature-checked.
"""

from __future__ import annotations

import logging
import uuid
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse

from crewquarters_broker.deps import BrokerState, broker_state
from crewquarters_shared.errors import PlatformError

log = logging.getLogger("crewquarters.broker.callbacks")

router = APIRouter()

BINDING_COOKIE = "cq_oauth_binding"
BINDING_COOKIE_PATH = "/api/v1/connections/google"
MAX_CALLBACK_BYTES = 64 * 1024


@router.get("/api/v1/connections/google/callback", include_in_schema=False)
async def google_callback(
    request: Request, state: BrokerState = Depends(broker_state)
) -> RedirectResponse:
    params = request.query_params
    result = {"result": "connected"}
    try:
        await state.google.callback(
            state=params.get("state", ""),
            code=params.get("code"),
            error=params.get("error"),
            binding=request.cookies.get(BINDING_COOKIE),
        )
    except PlatformError as exc:
        log.warning("google oauth callback rejected: %s", exc.code)
        result = {"result": "error", "code": exc.code}
    base = state.settings.public_base_url.rstrip("/")
    response = RedirectResponse(f"{base}/connections/google?{urlencode(result)}", 303)
    response.delete_cookie(BINDING_COOKIE, path=BINDING_COOKIE_PATH)
    return response


def _too_large() -> PlatformError:
    return PlatformError("PAYLOAD_TOO_LARGE", "Callback body is too large.", 413)


async def _bounded_body(request: Request) -> bytes:
    """The body, refusing more than ``MAX_CALLBACK_BYTES`` before reading it when the
    length is declared, and while streaming it when it is not."""
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_CALLBACK_BYTES):
        raise _too_large()
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_CALLBACK_BYTES:
            raise _too_large()
    return bytes(body)


async def _twilio_params(request: Request, state: BrokerState) -> dict[str, str]:
    body = await _bounded_body(request)
    params = dict(parse_qsl(body.decode("utf-8", errors="replace"), keep_blank_values=True))
    path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    await state.telephony.verify_callback(path, params, request.headers.get("x-twilio-signature"))
    return params


def _twiml(xml: str) -> Response:
    return Response(xml, media_type="application/xml")


@router.post("/api/v1/callbacks/twilio/voice/{call_id}", include_in_schema=False)
async def twilio_voice(
    call_id: uuid.UUID, request: Request, state: BrokerState = Depends(broker_state)
) -> Response:
    params = await _twilio_params(request, state)
    return _twiml(await state.telephony.voice(call_id, params))


@router.post("/api/v1/callbacks/twilio/gather/{call_id}", include_in_schema=False)
async def twilio_gather(
    call_id: uuid.UUID, request: Request, state: BrokerState = Depends(broker_state)
) -> Response:
    params = await _twilio_params(request, state)
    return _twiml(await state.telephony.gather(call_id, params))


@router.post("/api/v1/callbacks/twilio/status/{call_id}", include_in_schema=False)
async def twilio_status(
    call_id: uuid.UUID, request: Request, state: BrokerState = Depends(broker_state)
) -> Response:
    params = await _twilio_params(request, state)
    await state.telephony.status_callback(call_id, params)
    return Response(status_code=204)
