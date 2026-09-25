"""Broker telephony routes. The broker, not the agent, builds the call script (TwiML).

Like the real broker (``crewquarters_broker.agent_api``), only the owner-approved script is
accepted, with ``{name}`` filled in by a plain name, and only the approved disclosure; keep
the rules here identical to the real ones so an agent that passes here passes there.
"""

from __future__ import annotations

import itertools
import re
import unicodedata
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from crewquarters_fake.broker.audit import audited, require_connection
from crewquarters_fake.broker.auth import RunAuth, require, run_auth
from crewquarters_fake.errors import ApiError

router = APIRouter()
CAPABILITY = "twilio.call.fixed_script"
# The real broker's disclosure when the installation config has none.
DISCLOSURE = "Hello. This is an automated demo call from Crewquarters."
MAX_NAME = 40
_NAME_PUNCTUATION = frozenset(" '\u2019-.")


def plain_name(name: str) -> bool:
    """1-40 characters, starting with a letter, made of letters (with their combining
    marks), spaces, apostrophes, hyphens and periods; no digits or other punctuation; no
    leading, trailing or repeated separators except ". "; a period only ends the name or
    follows a one-letter initial."""
    if not 1 <= len(name) <= MAX_NAME or not unicodedata.category(name[0]).startswith("L"):
        return False
    if name != name.strip():
        return False
    if not all(unicodedata.category(c)[0] in "LM" or c in _NAME_PUNCTUATION for c in name):
        return False
    if any(
        a in _NAME_PUNCTUATION and b in _NAME_PUNCTUATION and a + b != ". "
        for a, b in itertools.pairwise(name)
    ):
        return False
    return all(
        i == len(name) - 1 or (i == 1 or name[i - 2] in _NAME_PUNCTUATION)
        for i, c in enumerate(name)
        if c == "."
    )


def approved_script(template: str, text: str) -> bool:
    """``text`` is the template with every ``{name}`` replaced by the same plain name."""
    parts = [re.escape(p) for p in template.split("{name}")]
    if len(parts) == 1:
        return text == template
    pattern = parts[0] + "(?P<name>.+?)" + "(?P=name)".join(parts[1:])
    match = re.fullmatch(pattern, text, re.DOTALL)
    return match is not None and plain_name(match.group("name"))


def _approved(config: dict[str, Any], script: ScriptIn) -> dict[str, Any]:
    """The script and disclosure the broker will speak, or the real broker's error."""
    template = config.get("script")
    if not isinstance(template, str) or not template:
        raise ApiError(
            409, "NEEDS_CONFIGURATION", "The installation config has no script.", {"key": "script"}
        )
    if not approved_script(template, script.text):
        raise ApiError(403, "PERMISSION_DENIED", "The call script must be the approved script.")
    disclosure = config.get("disclosure")
    if not isinstance(disclosure, str) or not disclosure:
        disclosure = DISCLOSURE
    elif script.disclosure != disclosure:
        raise ApiError(403, "PERMISSION_DENIED", "The disclosure must be the approved disclosure.")
    return {"disclosure": disclosure, "text": script.text}


class ScriptIn(BaseModel):
    disclosure: str = Field(min_length=1)
    text: str = Field(min_length=1)


class GatherIn(BaseModel):
    input: Literal["speech"]
    timeoutSeconds: int = Field(ge=1, le=60)


class CallIn(BaseModel):
    to: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")
    script: ScriptIn
    gather: GatherIn
    idempotencyKey: str = Field(min_length=1, max_length=200)


@router.post("/telephony/calls")
async def create_call(
    body: CallIn, request: Request, auth: RunAuth = Depends(run_auth)
) -> dict[str, Any]:
    require(auth, CAPABILITY, "broker.telephony.create")
    script = _approved(auth.installation.config, body.script)
    require_connection(auth, "twilio")

    async def call() -> dict[str, Any]:
        return auth.store.twilio.create(
            auth.run.id,
            body.to,
            script,
            body.gather.model_dump(),
            body.idempotencyKey,
        )

    return await audited(auth, request, "twilio", "broker.telephony.create", call)


@router.get("/telephony/calls/{call_id}")
async def get_call(
    call_id: str, request: Request, auth: RunAuth = Depends(run_auth)
) -> dict[str, Any]:
    require(auth, CAPABILITY, "broker.telephony.get")
    require_connection(auth, "twilio")

    async def call() -> dict[str, Any]:
        found = auth.store.twilio.find(call_id)
        if found is None or found.run_id != auth.run.id:
            raise ApiError(404, "NOT_FOUND", f"call {call_id} not found")
        return auth.store.twilio.advance(found)

    return await audited(auth, request, "twilio", "broker.telephony.get", call)
