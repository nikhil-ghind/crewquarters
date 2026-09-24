"""In-memory state of the fake platform (stands in for PostgreSQL).

All mutations are synchronous and run on the server's single event loop, so each one is atomic.
Callers that change state call ``await store.notify()`` so long-polls and SSE streams wake up.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from crewquarters_fake.faults import FaultRegistry
from crewquarters_fake.gateway import Gateway
from crewquarters_fake.knowledge import KnowledgeIndex
from crewquarters_fake.providers.gmail import GmailProvider
from crewquarters_fake.providers.sheets import SheetsProvider
from crewquarters_fake.providers.twilio import TwilioProvider
from crewquarters_fake.settings import FakeSettings
from crewquarters_fake.statemachine import check_transition
from crewquarters_fake.timeutil import iso, utcnow

__all__ = ["iso", "utcnow"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


@dataclass
class CatalogEntry:
    agent_id: str
    version: str
    name: str
    manifest: dict[str, Any]
    image_digest: str | None
    trust_status: str


@dataclass
class Installation:
    id: str
    agent_id: str
    version: str
    manifest: dict[str, Any]
    config: dict[str, Any]
    approved_permissions: dict[str, Any]
    capabilities: frozenset[str]
    resolved_profiles: list[str]
    knowledge_base_ids: list[str]
    enabled: bool = True
    revision: int = 1


@dataclass
class Attempt:
    number: int
    token: str
    outcome: str | None = None
    exit_code: int | None = None


@dataclass
class Run:
    id: str
    installation_id: str
    agent_id: str
    agent_version: str
    trigger: str
    scheduled_for: str | None
    created_at: datetime
    updated_at: datetime
    state: str = "QUEUED"
    attempts: list[Attempt] = field(default_factory=list)
    result: Any = None
    error: dict[str, Any] | None = None
    waiting_since: float | None = None
    waited_seconds: float = 0.0
    sequence: int = 0
    client_event_ids: set[str] = field(default_factory=set)

    @property
    def current_attempt(self) -> int:
        return len(self.attempts)

    @property
    def attempt(self) -> Attempt | None:
        return self.attempts[-1] if self.attempts else None


@dataclass
class Event:
    run_id: str
    attempt: int
    sequence: int
    type: str
    created_at: datetime
    payload: dict[str, Any]


@dataclass
class InputRequest:
    id: str
    run_id: str
    agent_id: str
    key: str
    title: str
    prompt: str
    schema: dict[str, Any]
    choices: list[dict[str, Any]] | None
    preview: list[dict[str, Any]] | None
    consequence: str | None
    timeout_seconds: int
    content_hash: str
    created_at: datetime
    deadline: datetime
    state: str = "pending"
    version: int = 1
    answer: dict[str, Any] | None = None


@dataclass
class IdempotencyRecord:
    key: str
    state: str
    result: Any
    claimed_by_attempt: int
    completed_at: datetime | None = None


@dataclass
class AutoAnswer:
    key_pattern: str
    data: Any
    delay_seconds: float = 0.0


class Store:
    def __init__(self, settings: FakeSettings) -> None:
        self.settings = settings
        self.faults = FaultRegistry()
        self.changed = asyncio.Condition()
        self._tasks: set[asyncio.Task[Any]] = set()
        self.reset()

    def reset(self) -> None:
        self.catalog: dict[tuple[str, str], CatalogEntry] = {}
        self.installations: dict[str, Installation] = {}
        self.runs: dict[str, Run] = {}
        self.run_keys: dict[str, str] = {}
        self.events: dict[str, list[Event]] = {}
        self.inputs: dict[str, InputRequest] = {}
        self.input_keys: dict[tuple[str, str], str] = {}
        self.idempotency: dict[tuple[str, str], IdempotencyRecord] = {}
        self.tokens: dict[str, tuple[str, int]] = {}
        self.connections: dict[str, str] = {"google": "connected", "twilio": "connected"}
        self.auto_answers: list[AutoAnswer] = []
        self.traffic: list[dict[str, Any]] = []
        self.faults.clear()
        self.reset_providers()

    def reset_providers(self) -> None:
        self.connections = {"google": "connected", "twilio": "connected"}
        self.auto_answers = []
        self.gmail = GmailProvider()
        self.sheets = SheetsProvider()
        self.twilio = TwilioProvider()
        self.knowledge = KnowledgeIndex()
        self.gateway = Gateway(self.settings)

    # --- events -------------------------------------------------------------------------------
    def append_event(self, run: Run, event_type: str, payload: dict[str, Any]) -> Event:
        run.sequence += 1
        event = Event(run.id, run.current_attempt, run.sequence, event_type, utcnow(), payload)
        self.events.setdefault(run.id, []).append(event)
        return event

    def events_after(self, run_id: str, after: int) -> list[Event]:
        return [e for e in self.events.get(run_id, []) if e.sequence > after]

    # --- run state ----------------------------------------------------------------------------
    def transition(self, run: Run, target: str, reason: str | None = None) -> None:
        check_transition(run.state, target)
        now = time.monotonic()
        if run.state == "WAITING_INPUT" and run.waiting_since is not None:
            run.waited_seconds += now - run.waiting_since
            run.waiting_since = None
        if target == "WAITING_INPUT":
            run.waiting_since = now
        previous, run.state = run.state, target
        run.updated_at = utcnow()
        self.append_event(run, "status", {"from": previous, "to": target, "reason": reason})

    def input_wait_used(self, run: Run) -> float:
        used = run.waited_seconds
        if run.waiting_since is not None:
            used += time.monotonic() - run.waiting_since
        return used

    # --- waiting ------------------------------------------------------------------------------
    async def notify(self) -> None:
        async with self.changed:
            self.changed.notify_all()

    async def wait_until(self, predicate: Callable[[], bool], seconds: float) -> bool:
        """Wait until ``predicate()`` is true or ``seconds`` pass. Returns the final predicate."""
        deadline = time.monotonic() + max(0.0, seconds)
        async with self.changed:
            while not predicate():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                try:
                    await asyncio.wait_for(self.changed.wait(), remaining)
                except TimeoutError:
                    return predicate()
        return True

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
