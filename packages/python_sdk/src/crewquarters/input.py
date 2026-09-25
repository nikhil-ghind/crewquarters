"""Operator input requests ("Crew Requests"): ask a question and wait for the answer."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote

from crewquarters._transport import BrokerClient
from crewquarters.errors import Cancelled, InputTimeout, InvalidInput

if TYPE_CHECKING:
    from crewquarters.context import Limits

Style = Literal["primary", "secondary", "danger"]
POLL_SECONDS = 25
# Input keys are part of the request identity and URL-safe by construction.
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


@dataclass(frozen=True)
class Choice:
    value: str
    label: str | None = None
    style: Style = "secondary"


@dataclass(frozen=True)
class InputAnswer:
    data: Any
    """The answer object exactly as submitted (it validates against the request's schema)."""
    value: Any
    """For a choices question, the chosen value; otherwise the same as ``data``."""
    answered_at: datetime | None


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def key_value_block(items: Iterable[tuple[str, str]]) -> dict[str, Any]:
    return {
        "type": "keyValue",
        "items": [{"label": label, "value": value} for label, value in items],
    }


def table_block(columns: Sequence[str], rows: Iterable[Sequence[object]]) -> dict[str, Any]:
    return {
        "type": "table",
        "columns": list(columns),
        "rows": [[str(cell) for cell in row] for row in rows],
    }


def _normalise_choices(choices: Sequence[Choice | str]) -> list[Choice]:
    normalised: list[Choice] = []
    for index, choice in enumerate(choices):
        if isinstance(choice, str):
            choice = Choice(choice, choice, "primary" if index == 0 else "secondary")
        normalised.append(Choice(choice.value, choice.label or choice.value, choice.style))
    return normalised


class InputClient:
    def __init__(
        self,
        transport: BrokerClient,
        limits: Limits,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._budget = limits.input_wait_remaining_seconds
        self._clock = clock
        self._waited = 0.0

    @property
    def remaining_wait_seconds(self) -> float:
        return max(0.0, self._budget - self._waited)

    async def ask(
        self,
        key: str,
        title: str,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        choices: Sequence[Choice | str] | None = None,
        preview: Sequence[dict[str, Any]] | None = None,
        consequence: str | None = None,
        timeout_seconds: int,
    ) -> InputAnswer:
        if not KEY_PATTERN.match(key):
            raise InvalidInput(
                "input keys are 1-128 characters of letters, digits, '_', '.', ':' or '-'"
            )
        if (schema is None) == (choices is None):
            raise InvalidInput("pass exactly one of schema or choices")
        if timeout_seconds < 1:
            raise InvalidInput("timeout_seconds must be at least 1")
        if timeout_seconds > self.remaining_wait_seconds:
            raise InvalidInput(
                f"timeout_seconds {timeout_seconds} exceeds the remaining input-wait budget "
                f"of {int(self.remaining_wait_seconds)} seconds",
                code="INPUT_WAIT_BUDGET_EXCEEDED",
            )
        body: dict[str, Any] = {
            "key": key,
            "title": title,
            "prompt": prompt,
            "timeoutSeconds": timeout_seconds,
        }
        # The control plane stores one preview object that the approval card renders.
        card: dict[str, Any] = {}
        if choices is not None:
            normalised = _normalise_choices(choices)
            if not normalised:
                raise InvalidInput("choices must not be empty")
            values = [c.value for c in normalised]
            body["schema"] = {
                "type": "object",
                "required": ["choice"],
                "properties": {"choice": {"type": "string", "enum": values}},
            }
            card["choices"] = [
                {"value": c.value, "label": c.label, "style": c.style} for c in normalised
            ]
        else:
            body["schema"] = schema
        if preview is not None:
            card["blocks"] = list(preview)
        if consequence is not None:
            card["consequence"] = consequence
        if card:
            body["preview"] = {"blocks": [], **card}

        request = await self._transport.request(
            "POST", "/input-requests", operation="input.create", idempotent=True, json=body
        )
        started = self._clock()
        try:
            while request["state"] == "pending":
                request = await self._transport.request(
                    "GET",
                    f"/input-requests/{quote(str(request['id']), safe='')}",
                    operation="input.get",
                    idempotent=True,
                    params={"wait": POLL_SECONDS},
                    read_timeout=POLL_SECONDS + 10,
                )
        finally:
            self._waited += self._clock() - started

        state = request["state"]
        if state == "expired":
            raise InputTimeout(f"input request {key} expired without an answer")
        if state == "cancelled":
            raise Cancelled(f"input request {key} was cancelled")
        data = request.get("answer")
        value = data.get("choice") if choices is not None and isinstance(data, dict) else data
        answered_at = request.get("answeredAt")
        return InputAnswer(
            data=data,
            value=value,
            answered_at=datetime.fromisoformat(answered_at)
            if isinstance(answered_at, str)
            else None,
        )
