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

from crewquarters_fake.crewq_gateway import CrewquartersGateway
from crewquarters_fake.faults import FaultRegistry
from crewquarters_fake.gateway import Gateway
from crewquarters_fake.knowledge import KnowledgeIndex
from crewquarters_fake.providers.github import GitHubProvider
from crewquarters_fake.providers.gmail import GmailProvider
from crewquarters_fake.providers.sheets import SheetsProvider
from crewquarters_fake.providers.twilio import TwilioProvider
from crewquarters_fake.settings import FakeSettings
from crewquarters_fake.speech import LocalSpeech, RemoteSpeech, SpeechService
from crewquarters_fake.statemachine import check_transition
from crewquarters_fake.timeutil import iso, utcnow
from crewquarters_fake.voice.backend import VoiceBackend
from crewquarters_fake.voice.scenario import CalleeScenario
from crewquarters_speech.fake import FakeSpeechEngine

__all__ = ["iso", "utcnow"]


def _crewq_gateway(settings: FakeSettings) -> CrewquartersGateway | None:
    if not settings.gateway_url:
        return None
    if not (settings.gateway_service_token and settings.gateway_voice_token):
        raise ValueError("CREWQ_FAKE_GATEWAY_URL needs the service and voice client tokens")
    return CrewquartersGateway(
        settings.gateway_url,
        settings.gateway_service_token,
        settings.gateway_voice_token,
        stt_model=settings.gateway_stt_model,
        tts_model=settings.gateway_tts_model,
        voice=settings.gateway_voice,
    )


def _voice_backend(settings: FakeSettings) -> VoiceBackend:
    """A real local LiveKit server when one is configured, otherwise the offline state machine."""
    if settings.livekit_url:
        from crewquarters_fake.voice.livekit import LiveKitVoiceBackend

        return LiveKitVoiceBackend(settings)
    from crewquarters_fake.voice.offline import OfflineVoiceBackend

    return OfflineVoiceBackend()


def new_id(prefix: str = "") -> str:
    """UUIDs, like the control plane (the prefix argument is accepted for readability only)."""
    return str(uuid.uuid4())


@dataclass
class CatalogEntry:
    agent_id: str
    version: str
    name: str
    manifest: dict[str, Any]
    image_digest: str | None
    trust_status: str
    version_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Installation:
    id: str
    agent_id: str
    version: str
    manifest: dict[str, Any]
    config: dict[str, Any]
    approved_permissions: dict[str, Any]
    capabilities: frozenset[str]
    model_bindings: dict[str, str]
    knowledge_base_ids: list[str]
    enabled: bool = True
    revision: int = 1
    created_at: datetime = field(default_factory=utcnow)

    @property
    def resolved_profiles(self) -> list[str]:
        """The exact model variants this installation may use."""
        return sorted(set(self.model_bindings.values()))


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
    agent_name: str = ""
    state: str = "QUEUED"
    # Like the control plane: a run starts at attempt 1 and each owner retry adds one.
    current_attempt: int = 1
    attempts: dict[int, Attempt] = field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    retryable: bool = False
    cancel_requested: bool = False
    result: Any = None
    error: dict[str, Any] | None = None
    waiting_since: float | None = None
    waited_seconds: float = 0.0
    sequence: int = 0
    client_event_ids: set[str] = field(default_factory=set)
    # Runs an agent started (trigger "agent"): the starter, its idempotency key, the input.
    parent_run_id: str | None = None
    start_key: str | None = None
    trigger_input: dict[str, Any] | None = None

    @property
    def attempt(self) -> Attempt | None:
        """The current attempt once it has been dispatched."""
        return self.attempts.get(self.current_attempt)


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
    attempt: int
    title: str
    prompt: str
    schema: dict[str, Any]
    preview: dict[str, Any] | None
    timeout_seconds: int
    created_at: datetime
    deadline: datetime
    state: str = "pending"
    version: int = 1
    answer: Any = None
    answered_at: datetime | None = None


@dataclass
class VoiceCallRecord:
    """A phone conversation placed through the voice backend (``ctx.voice``)."""

    id: str
    run_id: str
    idempotency_key: str
    to: str  # full E.164 number: never leaves the broker (views show toMasked)
    ring_timeout: float
    max_duration: float
    created_at: datetime
    updated_at: datetime
    state: str = "dialing"
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    error_code: str | None = None
    callee_identity: str = "callee"

    @property
    def room_name(self) -> str:
        return f"call-{self.id}"

    @property
    def duration_seconds(self) -> int | None:
        if self.answered_at is None or self.ended_at is None:
            return None
        return max(0, round((self.ended_at - self.answered_at).total_seconds()))


@dataclass
class ActionRecord:
    """An external-action key (``ctx.idempotency``): claimed, in doubt, or completed."""

    key: str
    status: str
    attempt: int
    result: Any = None
    completed_at: datetime | None = None


@dataclass
class AutoAnswer:
    key_pattern: str
    value: Any
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
        self.actions: dict[tuple[str, str], ActionRecord] = {}
        self.tokens: dict[str, tuple[str, int]] = {}
        self.connections: dict[str, str] = {
            "google": "connected",
            "twilio": "connected",
            "github": "connected",
        }
        self.auto_answers: list[AutoAnswer] = []
        self.traffic: list[dict[str, Any]] = []
        self.audit: list[dict[str, Any]] = []
        self.voice_calls: dict[str, VoiceCallRecord] = {}
        self.voice_keys: dict[tuple[str, str], str] = {}
        self.faults.clear()
        self.reset_providers()

    def reset_providers(self) -> None:
        self.connections = {"google": "connected", "twilio": "connected", "github": "connected"}
        self.auto_answers = []
        self.gmail = GmailProvider()
        self.sheets = SheetsProvider()
        self.twilio = TwilioProvider()
        self.github = GitHubProvider()
        self.knowledge = KnowledgeIndex()
        self.gateway = Gateway(self.settings)
        # The fake engine always exists: a simulated callee queues its lines here.
        self.fake_speech = FakeSpeechEngine()
        self.crewq_gateway = _crewq_gateway(self.settings)
        self.speech: SpeechService = (
            self.crewq_gateway
            or (RemoteSpeech(self.settings.speech_url) if self.settings.speech_url else None)
            or LocalSpeech(self.fake_speech)
        )
        self.voice_callees: dict[str, CalleeScenario] = {}
        self.voice_backend = _voice_backend(self.settings)

    def new_voice_call(
        self, run_id: str, key: str, to: str, *, ring_timeout: float, max_duration: float
    ) -> VoiceCallRecord:
        now = utcnow()
        call = VoiceCallRecord(new_id(), run_id, key, to, ring_timeout, max_duration, now, now)
        self.voice_calls[call.id] = call
        self.voice_keys[(run_id, key)] = call.id
        return call

    # --- events -------------------------------------------------------------------------------
    def append_event(self, run: Run, event_type: str, payload: dict[str, Any]) -> Event:
        run.sequence += 1
        event = Event(run.id, run.current_attempt, run.sequence, event_type, utcnow(), payload)
        self.events.setdefault(run.id, []).append(event)
        return event

    def events_after(self, run_id: str, after: int) -> list[Event]:
        return [e for e in self.events.get(run_id, []) if e.sequence > after]

    def audit_event(self, run: Run, action: str, payload: dict[str, Any]) -> None:
        """Security and usage records (LLM calls, connector calls, capability denials).

        The control plane keeps these in ``audit_events``, not in the run event stream."""
        self.audit.append(
            {"runId": run.id, "action": action, "createdAt": iso(utcnow()), **payload}
        )

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
        if target == "RUNNING" and run.started_at is None:
            run.started_at = run.updated_at
        if target in {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}:
            run.finished_at = run.updated_at
        elif target == "QUEUED":
            run.finished_at = None
        payload: dict[str, Any] = {"from": previous, "to": target}
        if reason:
            payload["reason"] = reason
        if target in {"FAILED", "INTERRUPTED"} and run.error:
            payload["errorCode"] = str(run.error.get("code"))
        self.append_event(run, "run.state_changed", payload)

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
