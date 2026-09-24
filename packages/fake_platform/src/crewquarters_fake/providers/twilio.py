"""Twilio outbound-call stand-in: scripted outcomes per number, one state step per status poll."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from crewquarters.redact import mask_phone
from crewquarters_fake.timeutil import iso, utcnow

TERMINAL = frozenset({"completed", "busy", "no-answer", "failed", "canceled"})


@dataclass
class FakeCall:
    id: str
    run_id: str
    key: str
    to: str
    script: dict[str, Any]
    gather: dict[str, Any]
    outcome: dict[str, Any]
    steps: list[str]
    created_at: datetime
    updated_at: datetime
    step: int = 0
    history: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        return self.steps[self.step]


def _steps(status: str) -> list[str]:
    if status == "completed":
        return ["queued", "ringing", "in-progress", "completed"]
    if status == "failed":
        return ["queued", "failed"]
    return ["queued", "ringing", status]


class TwilioProvider:
    def __init__(self) -> None:
        self.outcomes: dict[str, dict[str, Any]] = {}
        self.calls: dict[str, FakeCall] = {}
        self._by_key: dict[tuple[str, str], str] = {}
        self._counts: dict[str, int] = {}

    def load(self, outcomes: dict[str, dict[str, Any]]) -> None:
        self.outcomes = dict(outcomes)

    def create(
        self, run_id: str, to: str, script: dict[str, Any], gather: dict[str, Any], key: str
    ) -> dict[str, Any]:
        existing = self._by_key.get((run_id, key))
        if existing is not None:
            return self.view(self.calls[existing])
        outcome = self.outcomes.get(to) or {"status": "failed", "errorCode": "unverified-number"}
        now = utcnow()
        call = FakeCall(
            id="CA" + uuid.uuid4().hex,
            run_id=run_id,
            key=key,
            to=to,
            script=script,
            gather=gather,
            outcome=outcome,
            steps=_steps(str(outcome.get("status", "failed"))),
            created_at=now,
            updated_at=now,
        )
        self.calls[call.id] = call
        self._by_key[(run_id, key)] = call.id
        self._counts[to] = self._counts.get(to, 0) + 1
        return self.view(call)

    def find(self, call_id: str) -> FakeCall | None:
        return self.calls.get(call_id)

    def advance(self, call: FakeCall) -> dict[str, Any]:
        if call.step < len(call.steps) - 1:
            call.step += 1
            call.updated_at = utcnow()
        return self.view(call)

    def get(self, call_id: str) -> dict[str, Any]:
        return self.advance(self.calls[call_id])

    def view(self, call: FakeCall) -> dict[str, Any]:
        final = call.state in TERMINAL
        answered = call.state in {"in-progress", "completed"}
        speech = call.outcome.get("speech") if final and call.state == "completed" else None
        return {
            "id": call.id,
            "idempotencyKey": call.key,
            "toMasked": mask_phone(call.to),
            "state": call.state,
            "answered": answered,
            "speechCaptured": bool(speech),
            "transcript": speech or None,
            "durationSeconds": int(call.outcome.get("durationSeconds", 14)) if final and answered else None,
            "errorCode": call.outcome.get("errorCode") if call.state == "failed" else None,
            "createdAt": iso(call.created_at),
            "updatedAt": iso(call.updated_at),
        }

    def calls_by_number(self) -> dict[str, int]:
        return dict(self._counts)

    def snapshot(self) -> dict[str, Any]:
        return {
            "byNumber": self.calls_by_number(),
            "calls": [
                {**self.view(c), "to": c.to, "runId": c.run_id, "script": c.script}
                for c in self.calls.values()
            ],
        }
