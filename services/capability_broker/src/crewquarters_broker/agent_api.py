"""The agent-facing API (``/agent/v1``): the only endpoint an agent container can reach.

Every route requires ``Authorization: Bearer $PLATFORM_RUN_TOKEN`` and is checked by
:func:`crewquarters_broker.auth.authorize`. The run and attempt always come from the
token, never from the request. Resource IDs (spreadsheet, knowledge base) come from the
owner-approved installation config, never from the agent.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import Field

from crewquarters_broker.auth import Grant
from crewquarters_broker.deps import ApiModel, BrokerState, agent_grant, broker_state
from crewquarters_shared.errors import PlatformError, invalid, not_found

router = APIRouter(prefix="/agent/v1")

KEY_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"
RANGE_PATTERN = r"^[^\x00-\x1f]{1,200}$"
Cell = str | int | float | bool | None
_UNSAFE_VARIABLE = re.compile(r"[\x00-\x1f{}<>]")


class EventIn(ApiModel):
    type: Literal["run.log", "run.progress", "run.metric", "run.artifact"]
    payload: dict[str, Any]


class ResultIn(ApiModel):
    status: Literal["succeeded", "failed"]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class AskIn(ApiModel):
    key: str = Field(pattern=KEY_PATTERN)
    title: str
    prompt: str
    schema_: dict[str, Any] = Field(alias="schema")
    timeout_seconds: int
    preview: dict[str, Any] | None = None


class CompleteIn(ApiModel):
    result: Any = None


class SearchIn(ApiModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(8, ge=1, le=50)
    max_context_tokens: int = Field(5000, ge=100, le=20000)
    filters: dict[str, Any] = Field(default_factory=dict)


class ValuesIn(ApiModel):
    range: str = Field(pattern=RANGE_PATTERN)
    values: list[list[Cell]] = Field(min_length=1, max_length=1000)


class CallIn(ApiModel):
    to: str
    idempotency_key: str = Field(pattern=KEY_PATTERN)
    variables: dict[Literal["name"], str] = Field(default_factory=dict)


def _body(grant: Grant, **fields: Any) -> dict[str, Any]:
    return {"attempt": grant.attempt, **fields}


# --- Run lifecycle (forwarded to the control API with the token's attempt) ------------


@router.post("/handshake", summary="SDK handshake: PREPARING -> RUNNING")
async def handshake(
    grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Any:
    return await state.control.request("POST", f"/runs/{grant.run_id}/handshake", json=_body(grant))


@router.post("/heartbeat", summary="Extend the attempt lease; returns cancelRequested")
async def heartbeat(
    grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Any:
    return await state.control.request("POST", f"/runs/{grant.run_id}/heartbeat", json=_body(grant))


@router.post("/events", summary="Append a log, progress, metric, or artifact event")
async def event(
    body: EventIn, grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Any:
    grant.require("events.write")
    return await state.control.request(
        "POST", f"/runs/{grant.run_id}/events", json=_body(grant, **body.model_dump())
    )


@router.post("/result", summary="Post the final result; the first result wins")
async def result(
    body: ResultIn, grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Any:
    return await state.control.request(
        "POST", f"/runs/{grant.run_id}/result", json=_body(grant, **body.model_dump())
    )


@router.post("/input-requests", summary="Ask the owner a question (stable key)")
async def ask(
    body: AskIn, grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Any:
    grant.require("user_input")
    return await state.control.request(
        "POST",
        f"/runs/{grant.run_id}/input-requests",
        json=_body(grant, **body.model_dump(by_alias=True)),
    )


@router.get("/input-requests/{request_id}", summary="Long-poll an input request")
async def poll_input(
    request_id: uuid.UUID,
    wait: int = Query(0, ge=0, le=30),
    grant: Grant = Depends(agent_grant),
    state: BrokerState = Depends(broker_state),
) -> Any:
    grant.require("user_input")
    found = await state.control.request(
        "GET", f"/input-requests/{request_id}", params={"wait": wait}
    )
    if found.get("runId") != str(grant.run_id):
        raise not_found("Input request", request_id)
    return found


@router.post("/actions/{key}/claim", summary="Claim an idempotent action key")
async def claim(
    key: str = Path(pattern=KEY_PATTERN),
    grant: Grant = Depends(agent_grant),
    state: BrokerState = Depends(broker_state),
) -> Any:
    grant.require("idempotency")
    return await state.control.request(
        "POST", f"/runs/{grant.run_id}/actions/{key}/claim", json=_body(grant)
    )


@router.post("/actions/{key}/complete", summary="Record an action's result")
async def complete(
    body: CompleteIn,
    key: str = Path(pattern=KEY_PATTERN),
    grant: Grant = Depends(agent_grant),
    state: BrokerState = Depends(broker_state),
) -> Any:
    grant.require("idempotency")
    return await state.control.request(
        "POST",
        f"/runs/{grant.run_id}/actions/{key}/complete",
        json=_body(grant, result=body.result),
    )


# --- Knowledge ---------------------------------------------------------------------------


@router.post("/knowledge/search", summary="Search the knowledge base chosen in config")
async def knowledge_search(
    body: SearchIn, grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Any:
    grant.require("knowledge.search:config")
    kb_id = grant.configured("knowledgeBaseId")
    return await state.knowledge.request(
        "POST", f"/knowledge-bases/{kb_id}/query", json=body.model_dump(by_alias=True)
    )


# --- Gmail ---------------------------------------------------------------------------------


@router.get("/google/gmail/messages", summary="List message IDs (Gmail search syntax)")
async def gmail_list(
    q: str = Query("", max_length=500),
    page_token: str | None = Query(None, alias="pageToken", max_length=200),
    max_results: int = Query(100, alias="maxResults", ge=1, le=500),
    grant: Grant = Depends(agent_grant),
    state: BrokerState = Depends(broker_state),
) -> Any:
    grant.require("google.gmail.readonly")
    return await state.google.gmail_list(q, page_token, max_results)


@router.get("/google/gmail/messages/{message_id}", summary="Get one sanitized message")
async def gmail_get(
    message_id: str,
    max_chars: int = Query(20_000, alias="maxChars", ge=100, le=100_000),
    grant: Grant = Depends(agent_grant),
    state: BrokerState = Depends(broker_state),
) -> Any:
    grant.require("google.gmail.readonly")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", message_id):
        raise invalid("INVALID_INPUT", "Invalid message id.")
    return await state.google.gmail_get(message_id, max_chars)


# --- Sheets (only the spreadsheet named in the installation config) ------------------------


@router.get("/google/sheets/values", summary="Read a range of the configured spreadsheet")
async def sheets_read(
    cell_range: str = Query(alias="range", pattern=RANGE_PATTERN),
    grant: Grant = Depends(agent_grant),
    state: BrokerState = Depends(broker_state),
) -> Any:
    grant.require("google.spreadsheets")
    return await state.google.sheets_read(grant.configured("spreadsheetId"), cell_range)


@router.post("/google/sheets/values:append", summary="Append rows to the configured spreadsheet")
async def sheets_append(
    body: ValuesIn, grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Any:
    grant.require("google.spreadsheets")
    grant.require_not_cancelled()
    return await state.google.sheets_append(
        grant.configured("spreadsheetId"), body.range, body.values
    )


@router.put("/google/sheets/values", summary="Overwrite a range of the configured spreadsheet")
async def sheets_update(
    body: ValuesIn, grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Any:
    grant.require("google.spreadsheets")
    grant.require_not_cancelled()
    return await state.google.sheets_update(
        grant.configured("spreadsheetId"), body.range, body.values
    )


# --- Telephony -----------------------------------------------------------------------------


def _bounded(config: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = config.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return max(low, min(high, value))


@router.post("/telephony/calls", summary="Place a fixed-script call (idempotent per key)")
async def create_call(
    body: CallIn, grant: Grant = Depends(agent_grant), state: BrokerState = Depends(broker_state)
) -> Any:
    grant.require("twilio.call.fixed_script")
    grant.require_not_cancelled()
    template = grant.configured("script")
    if len(template) > 1000:
        raise PlatformError("NEEDS_CONFIGURATION", "The configured script is too long.", 409)
    script = template
    for name, value in body.variables.items():
        script = script.replace("{" + name + "}", _UNSAFE_VARIABLE.sub("", value)[:80])
    return await state.telephony.create_call(
        run_id=grant.run_id,
        idempotency_key=body.idempotency_key,
        to=body.to,
        script=script,
        response_seconds=_bounded(grant.config, "responseSeconds", 20, 5, 60),
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
