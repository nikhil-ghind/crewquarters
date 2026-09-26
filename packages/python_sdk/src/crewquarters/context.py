"""The run context handed to an agent's run function."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from crewquarters._transport import BrokerClient
from crewquarters.camera import CameraClient
from crewquarters.events import EventsClient
from crewquarters.google import GoogleClients
from crewquarters.idempotency import IdempotencyClient
from crewquarters.input import InputClient
from crewquarters.knowledge import KnowledgeClient
from crewquarters.llm import LLMClient
from crewquarters.telephony import TelephonyClient


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class RunInfo:
    id: str
    attempt: int
    trigger: str
    scheduled_for: datetime | None
    installation_id: str
    agent_id: str
    agent_version: str
    created_at: datetime | None

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> RunInfo:
        return cls(
            id=str(data["id"]),
            attempt=int(data["attempt"]),
            trigger=str(data["trigger"]),
            scheduled_for=parse_time(data.get("scheduledFor")),
            installation_id=str(data["installationId"]),
            agent_id=str(data["agentId"]),
            agent_version=str(data["agentVersion"]),
            created_at=parse_time(data.get("createdAt")),
        )


@dataclass(frozen=True)
class Grants:
    llm_profiles: tuple[str, ...] = ()
    knowledge_base_ids: tuple[str, ...] = ()
    google: tuple[str, ...] = ()
    twilio: tuple[str, ...] = ()
    camera: tuple[str, ...] = ()
    cloud_providers: tuple[str, ...] = ()
    # Requested profile (family or exact) -> the variant the owner approved.
    model_bindings: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> Grants:
        def items(name: str) -> tuple[str, ...]:
            return tuple(str(v) for v in data.get(name, []))

        return cls(
            llm_profiles=items("llmProfiles"),
            knowledge_base_ids=items("knowledgeBaseIds"),
            google=items("google"),
            twilio=items("twilio"),
            camera=items("camera"),
            cloud_providers=items("cloudProviders"),
            model_bindings={str(k): str(v) for k, v in (data.get("modelBindings") or {}).items()},
        )


@dataclass(frozen=True)
class Limits:
    active_timeout_seconds: int
    input_wait_remaining_seconds: float

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> Limits:
        return cls(
            active_timeout_seconds=int(data.get("activeTimeoutSeconds", 0)),
            input_wait_remaining_seconds=float(data.get("inputWaitRemainingSeconds", 0)),
        )


class RunContext[ConfigT]:
    """Everything an agent needs for one run: run metadata, configuration, and platform clients."""

    def __init__(
        self,
        *,
        run: RunInfo,
        config: ConfigT,
        capabilities: frozenset[str],
        grants: Grants,
        limits: Limits,
        transport: BrokerClient,
        events: EventsClient,
    ) -> None:
        self.run = run
        self.config = config
        self.capabilities = capabilities
        self.grants = grants
        self.limits = limits
        self.events = events
        self.input = InputClient(transport, limits)
        self.idempotency = IdempotencyClient(transport)
        self.llm = LLMClient(transport, grants.llm_profiles)
        self.knowledge = KnowledgeClient(transport)
        self.google = GoogleClients(transport)
        self.telephony = TelephonyClient(transport)
        self.camera = CameraClient(transport)
