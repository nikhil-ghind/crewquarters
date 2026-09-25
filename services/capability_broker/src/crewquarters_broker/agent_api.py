"""The agent-facing SDK API (``/internal/v1/sdk``): the only endpoint an agent can reach.

Contract: ``packages/contracts/broker-sdk.openapi.yaml``. Every route requires
``Authorization: Bearer $PLATFORM_RUN_TOKEN`` and is checked by
:func:`crewquarters_broker.auth.authorize`. The run and attempt always come from the
token. Where the contract lets the agent name a resource (knowledge base, spreadsheet) or
send call text, the broker accepts it only if it matches the owner-approved config.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import Field

from crewquarters_broker.auth import Grant, authorize, bearer
from crewquarters_broker.deps import ApiModel, BrokerState, agent_grant, broker_state
from crewquarters_broker.errors import permission_denied
from crewquarters_broker.twilio import DISCLOSURE
from crewquarters_shared.errors import PlatformError

router = APIRouter(prefix="/internal/v1/sdk")

PROTOCOL = "v1alpha1"
KEY_PATTERN = r"^[A-Za-z0-9_.:-]{1,200}$"
CLAIM_TOKEN_HEADER = "X-Claim-Token"  # noqa: S105 - a header name
CLAIM_TOKEN_PATTERN = r"[A-Za-z0-9_-]{8,128}"  # noqa: S105 - a pattern
Cell = str | int | float | bool | None
_MAX_NAME = 100
_NAME = rf"[^\x00-\x1f<>{{}}]{{0,{_MAX_NAME}}}"


class HandshakeIn(ApiModel):
    protocol: str
    sdk_version: str
    agent_id: str


class AgentEvent(ApiModel):
    client_event_id: str = Field(min_length=1, max_length=64)
    type: Literal["run.log", "run.progress", "run.metric", "run.artifact"]
    occurred_at: datetime
    payload: dict[str, Any]


class EventsIn(ApiModel):
    events: list[AgentEvent] = Field(max_length=200)


class ResultIn(ApiModel):
    status: Literal["succeeded", "failed"]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class AskIn(ApiModel):
    key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    title: str
    prompt: str
    schema_: dict[str, Any] = Field(alias="schema")
    timeout_seconds: int
    preview: dict[str, Any] | None = None


class CompleteIn(ApiModel):
    result: Any = None


class ChatMessage(ApiModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatIn(ApiModel):
    profile: str = Field(min_length=1)
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = Field(None, ge=0, le=2)
    max_output_tokens: int | None = Field(None, ge=1)
    response_schema: dict[str, Any] | None = None
    tools: list[Any] = Field(default_factory=list, max_length=0)
    idempotency_key: str | None = None


class SearchIn(ApiModel):
    knowledge_base_id: str
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(8, ge=1, le=50)
    max_context_tokens: int | None = Field(None, ge=1)
    filters: dict[str, list[str]] = Field(default_factory=dict)


class RangeIn(ApiModel):
    spreadsheet_id: str
    range: str = Field(min_length=1, max_length=200)


class ValuesIn(RangeIn):
    values: list[list[Cell]] = Field(min_length=1, max_length=1000)


class CallScript(ApiModel):
    disclosure: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=1500)


class CallGather(ApiModel):
    input: Literal["speech"]
    timeout_seconds: int = Field(ge=1, le=60)


class CallIn(ApiModel):
    to: str
    script: CallScript
    gather: CallGather
    idempotency_key: str = Field(pattern=KEY_PATTERN)


def _attempt(grant: Grant, **fields: Any) -> dict[str, Any]:
    return {"attempt": grant.attempt, **fields}


async def finished_run_grant(request: Request, state: BrokerState = Depends(broker_state)) -> Grant:
    """Like ``agent_grant``, but a run that already ended still authorizes (same token,
    attempt, and ``jti``). Heartbeat and result use it so an agent whose run timed out or
    was cancelled hears ``cancelRequested: true`` and can report, as the contract says."""
    return await authorize(
        bearer(request),
        state.settings.capability_signing_key.get_secret_value(),
        state.control,
        allow_finished=True,
    )


# --- Run lifecycle (forwarded to the control API with the token's attempt) ----------------


def _grants(grant: Grant) -> dict[str, Any]:
    caps = grant.capabilities

    def after(prefix: str) -> list[str]:
        return sorted(c.removeprefix(prefix) for c in caps if c.startswith(prefix))

    kb = grant.config.get("knowledgeBaseId")
    return {
        "llmProfiles": after("llm.profile:"),
        "modelBindings": grant.run.get("modelBindings") or {},
        "knowledgeBaseIds": [kb] if "knowledge.search:config" in caps and kb else [],
        "google": after("google."),
        "twilio": after("twilio."),
        "cloudProviders": after("cloud."),
    }


@router.post("/handshake", summary="Start the attempt; returns the run context")
async def handshake(
    body: HandshakeIn,
    grant: Grant = Depends(agent_grant),
    state: BrokerState = Depends(broker_state),
) -> dict[str, Any]:
    if body.protocol != PROTOCOL:
        raise PlatformError(
            "PROTOCOL_UNSUPPORTED",
            f"This platform speaks {PROTOCOL}.",
            409,
            {"supported": [PROTOCOL]},
        )
    await state.control.request("POST", f"/runs/{grant.run_id}/handshake", json=_attempt(grant))
    run, claims = grant.run, grant.claims
    now = datetime.now(UTC)
    # The control API's run view supplies the trigger, schedule slot, manifest id and
    # semver, creation time, and time limits. The token-derived fallbacks only cover an
    # older control API that lacks those fields.
    token_seconds = max(0, int((claims.expires_at - now).total_seconds()))
    return {
        "run": {
            "id": str(grant.run_id),
            "attempt": grant.attempt,
            "trigger": run.get("trigger") or "manual",
            "scheduledFor": run.get("scheduledFor"),
            "installationId": claims.installation_id,
            "agentId": run.get("agentId") or body.agent_id,
            "agentVersion": run.get("agentVersion") or claims.agent_version_id,
            "createdAt": run.get("createdAt") or claims.issued_at.isoformat(),
        },
        "config": grant.config,
        "capabilities": sorted(grant.capabilities),
        "grants": _grants(grant),
        "limits": {
            "activeTimeoutSeconds": run.get("activeTimeoutSeconds") or token_seconds,
            "inputWaitRemainingSeconds": run.get("inputWaitRemainingSeconds", token_seconds),
        },
        "heartbeatIntervalSeconds": max(1.0, state.settings.heartbeat_timeout_seconds / 3),
        "serverTime": now.isoformat(),
    }


@router.post("/heartbeat", summary="Extend the attempt lease; returns cancelRequested")
async def heartbeat(
    grant: Grant = Depends(finished_run_grant), state: BrokerState = Depends(broker_state)
) -> Any:
    """For a run that already ended, the control API answers ``cancelRequested: true``."""
    return await state.control.request(
        "POST", f"/runs/{grant.run_id}/heartbeat", json=_attempt(grant)
    )


@router.post("/events", summary="Append a batch of log, progress, metric, artifact events")
async def events(
    body: EventsIn, grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> dict[str, int]:
    """The whole batch goes to the control API in one call, which validates every event
    first and stores all or none, skipping ``clientEventId`` values already stored for the
    run. A rejected batch returns 422 with ``details.rejected[]`` (index, clientEventId,
    code) and stores nothing, so the SDK's event-by-event resend never duplicates."""
    grant.require("events.write", baseline=True)
    out = await state.control.request(
        "POST",
        f"/runs/{grant.run_id}/event-batches",
        json=_attempt(
            grant,
            events=[
                {
                    "clientEventId": e.client_event_id,
                    "type": e.type,
                    "occurredAt": e.occurred_at.isoformat(),
                    "payload": e.payload,
                }
                for e in body.events
            ],
        ),
    )
    return {"accepted": int(out["accepted"]), "lastSequence": int(out["lastSequence"])}


@router.post("/result", summary="Post the final result; the first result wins")
async def result(
    body: ResultIn,
    grant: Grant = Depends(finished_run_grant),
    state: BrokerState = Depends(broker_state),
) -> Any:
    return await state.control.request(
        "POST", f"/runs/{grant.run_id}/result", json=_attempt(grant, **body.model_dump())
    )


@router.post("/input-requests", summary="Ask the owner a question (stable key)")
async def ask(
    body: AskIn, grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Any:
    grant.require("user_input")
    return await state.control.request(
        "POST",
        f"/runs/{grant.run_id}/input-requests",
        json=_attempt(grant, **body.model_dump(by_alias=True)),
    )


@router.get("/input-requests/{request_id}", summary="Long-poll an input request")
async def poll_input(
    request_id: uuid.UUID,
    wait: float = Query(0, ge=0, le=30),
    grant: Grant = Depends(agent_grant),
    state: BrokerState = Depends(broker_state),
) -> Any:
    grant.require("user_input")
    found = await state.control.request(
        "GET", f"/input-requests/{request_id}", params={"wait": int(wait)}
    )
    if found.get("runId") != str(grant.run_id):
        raise PlatformError("NOT_FOUND", "Input request not found.", 404)
    return found


@router.post("/actions/{key}/claim", summary="Claim an idempotent action key")
async def claim(
    request: Request,
    key: str = Path(pattern=KEY_PATTERN),
    grant: Grant = Depends(agent_grant),
    state: BrokerState = Depends(broker_state),
) -> Any:
    """``X-Claim-Token`` (random per SDK ``claim()`` call, reused on its retries) lets a
    retry after a lost response get its original ``claimed`` instead of ``in_doubt``."""
    grant.require("idempotency", baseline=True)
    body = _attempt(grant)
    token = request.headers.get(CLAIM_TOKEN_HEADER)
    if token is not None:
        if not re.fullmatch(CLAIM_TOKEN_PATTERN, token):
            raise PlatformError("INVALID_REQUEST", f"{CLAIM_TOKEN_HEADER} is malformed.", 422)
        body["claimToken"] = token
    return await state.control.request(
        "POST", f"/runs/{grant.run_id}/actions/{key}/claim", json=body
    )


@router.post("/actions/{key}/complete", summary="Record an action's result")
async def complete(
    body: CompleteIn,
    key: str = Path(pattern=KEY_PATTERN),
    grant: Grant = Depends(agent_grant),
    state: BrokerState = Depends(broker_state),
) -> Any:
    grant.require("idempotency", baseline=True)
    return await state.control.request(
        "POST",
        f"/runs/{grant.run_id}/actions/{key}/complete",
        json=_attempt(grant, result=body.result),
    )


# --- LLM (forwarded to the model gateway, which re-verifies the token) --------------------


def _require_llm(grant: Grant, profile: str) -> None:
    provider = profile.split(".", 1)[0]
    if provider in ("openai", "anthropic"):
        grant.require(f"cloud.{provider}")
    granted = next((c for c in sorted(grant.capabilities) if c.startswith("llm.profile:")), None)
    grant.require(granted or "llm.profile:<variant>")


def _relay(resp: httpx.Response) -> Response:
    return Response(
        resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


def _chat_request(state: BrokerState, grant: Grant, body: ChatIn, stream: bool) -> httpx.Request:
    payload = {**body.model_dump(by_alias=True, exclude_none=True), "stream": stream}
    return state.gateway.build_request(
        "POST", "/llm/chat", json=payload, headers={"x-capability-token": grant.token}
    )


@router.post("/llm/chat", summary="Chat with an approved model profile")
async def llm_chat(
    body: ChatIn, grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Response:
    _require_llm(grant, body.profile)
    try:
        resp = await state.gateway.send(_chat_request(state, grant, body, stream=False))
    except httpx.HTTPError:
        raise PlatformError("MODEL_UNAVAILABLE", "The model gateway is unreachable.", 503) from None
    return _relay(resp)


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


async def _translate(resp: httpx.Response) -> AsyncIterator[bytes]:
    """Gateway events ``{type: delta|done|error, ...}`` become the contract's SSE: ``delta``
    with ``{text}``, then one ``done`` with a ChatResponse or ``error`` with an envelope."""
    try:
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            event = json.loads(line.removeprefix("data:").strip())
            kind = event.get("type")
            if kind == "delta":
                yield _sse("delta", {"text": event.get("text", "")})
            elif kind == "done":
                yield _sse("done", event.get("response"))
            elif kind == "error":
                yield _sse("error", {"error": event.get("error")})
    finally:
        await resp.aclose()


@router.post("/llm/chat:stream", summary="Chat with streamed deltas (server-sent events)")
async def llm_chat_stream(
    body: ChatIn, grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Response:
    _require_llm(grant, body.profile)
    try:
        resp = await state.gateway.send(_chat_request(state, grant, body, stream=True), stream=True)
    except httpx.HTTPError:
        raise PlatformError("MODEL_UNAVAILABLE", "The model gateway is unreachable.", 503) from None
    if resp.status_code != 200:  # authorization and load failures arrive before streaming
        await resp.aread()
        await resp.aclose()
        return _relay(resp)
    return StreamingResponse(
        _translate(resp), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
    )


# --- Knowledge -----------------------------------------------------------------------------


@router.post("/knowledge/search", summary="Search the knowledge base chosen in config")
async def knowledge_search(
    body: SearchIn, grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Any:
    grant.require("knowledge.search:config")
    kb_id = grant.configured("knowledgeBaseId", body.knowledge_base_id)
    query: dict[str, Any] = {"query": body.query, "topK": body.top_k, "filters": body.filters}
    if body.max_context_tokens is not None:
        query["maxContextTokens"] = max(100, min(20_000, body.max_context_tokens))
    return await state.knowledge.request("POST", f"/knowledge-bases/{kb_id}/query", json=query)


# --- Gmail ---------------------------------------------------------------------------------


@router.get("/google/gmail/messages", summary="List message IDs (Gmail search syntax)")
async def gmail_list(
    q: str = Query("", max_length=500),
    page_token: str | None = Query(None, alias="pageToken", max_length=200),
    max_results: int = Query(100, alias="maxResults", ge=1, le=500),
    label_ids: list[str] = Query(default_factory=list, alias="labelIds", max_length=20),
    grant: Grant = Depends(agent_grant),
    state: BrokerState = Depends(broker_state),
) -> Any:
    grant.require("google.gmail.readonly")
    return await state.google.gmail_list(q, page_token, max_results, label_ids)


@router.get("/google/gmail/messages/{message_id}", summary="Get one message (format=full)")
async def gmail_get(
    message_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,64}$"),
    grant: Grant = Depends(agent_grant),
    state: BrokerState = Depends(broker_state),
) -> Any:
    grant.require("google.gmail.readonly")
    return await state.google.gmail_get(message_id)


# --- Sheets (only the spreadsheet named in the installation config) ------------------------


@router.post("/google/sheets/values:get", summary="Read a range of the configured spreadsheet")
async def sheets_get(
    body: RangeIn, grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Any:
    grant.require("google.spreadsheets")
    sheet = grant.configured("spreadsheetId", body.spreadsheet_id)
    return await state.google.sheets_read(sheet, body.range)


@router.post("/google/sheets/values:update", summary="Overwrite values at the range start")
async def sheets_update(
    body: ValuesIn, grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Any:
    grant.require("google.spreadsheets")
    sheet = grant.configured("spreadsheetId", body.spreadsheet_id)
    return await state.google.sheets_update(sheet, body.range, body.values)


@router.post("/google/sheets/values:append", summary="Append rows after the table")
async def sheets_append(
    body: ValuesIn, grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Any:
    grant.require("google.spreadsheets")
    sheet = grant.configured("spreadsheetId", body.spreadsheet_id)
    return await state.google.sheets_append(sheet, body.range, body.values)


# --- Telephony -----------------------------------------------------------------------------


def _bounded(config: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = config.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return max(low, min(high, value))


def approved_script(template: str, text: str) -> bool:
    """``text`` is the owner's template with every ``{name}`` replaced by one plain name."""
    parts = [re.escape(p) for p in template.split("{name}")]
    if len(parts) == 1:
        return text == template
    pattern = parts[0] + f"(?P<name>{_NAME})" + "(?P=name)".join(parts[1:])
    return re.fullmatch(pattern, text) is not None


@router.post("/telephony/calls", summary="Place the approved fixed-script call (idempotent)")
async def create_call(
    body: CallIn, grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Any:
    grant.require("twilio.call.fixed_script")
    if not approved_script(grant.configured("script"), body.script.text):
        raise permission_denied("The call script must be the approved script.")
    disclosure = grant.config.get("disclosure")
    if not isinstance(disclosure, str) or not disclosure:
        disclosure = DISCLOSURE
    elif body.script.disclosure != disclosure:
        raise permission_denied("The disclosure must be the approved disclosure.")
    return await state.telephony.create_call(
        run_id=grant.run_id,
        idempotency_key=body.idempotency_key,
        to=body.to,
        disclosure=disclosure,
        script=body.script.text,
        response_seconds=body.gather.timeout_seconds,
        max_calls=_bounded(grant.config, "maxCalls", 3, 1, 10),
    )


@router.get("/telephony/calls/{call_id}", summary="Call status and result")
async def get_call(
    call_id: uuid.UUID,
    grant: Grant = Depends(agent_grant),
    state: BrokerState = Depends(broker_state),
) -> Any:
    grant.require("twilio.call.fixed_script")
    return await state.telephony.get_call(grant.run_id, call_id)
