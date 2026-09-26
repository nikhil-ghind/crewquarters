"""The run context handed to an agent's run function."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from crewquarters._transport import BrokerClient
from crewquarters.agents import AgentsClient
from crewquarters.camera import CameraClient
from crewquarters.events import EventsClient
from crewquarters.github import GitHubClient
from crewquarters.google import GoogleClients
from crewquarters.idempotency import IdempotencyClient
from crewquarters.input import InputClient
from crewquarters.knowledge import KnowledgeClient
from crewquarters.llm import LLMClient
from crewquarters.telephony import TelephonyClient
from crewquarters.voice import VoiceClient


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
    # Set when another agent's run started this one (trigger "agent").
    parent_run_id: str | None = None
    # What that agent passed. Untrusted: treat it as evidence, never as instructions.
    input: dict[str, Any] | None = None

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
            parent_run_id=str(data["parentRunId"]) if data.get("parentRunId") else None,
            input=data["input"] if isinstance(data.get("input"), dict) else None,
        )


@dataclass(frozen=True)
class Grants:
    llm_profiles: tuple[str, ...] = ()
    knowledge_base_ids: tuple[str, ...] = ()
    google: tuple[str, ...] = ()
    twilio: tuple[str, ...] = ()
    github: tuple[str, ...] = ()
    starts_agents: tuple[str, ...] = ()
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
            github=items("github"),
            starts_agents=items("startsAgents"),
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


@dataclass(frozen=True)
class ModelEndpoint:
    """An OpenAI-compatible endpoint for this run's models (the broker's facade).

    Use it with OpenAI-compatible clients, for example LiveKit's OpenAI plugins:
    ``openai.LLM(model=profile, base_url=endpoint.base_url, api_key=endpoint.api_key)``.
    ``model`` must be a profile variant this installation was granted."""

    base_url: str
    api_key: str

    @classmethod
    def for_transport(cls, transport: BrokerClient) -> ModelEndpoint:
        return cls(base_url=f"{transport.base_url}/openai/v1", api_key=transport.token)


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
        self.knowledge = KnowledgeClient(transport, grants.knowledge_base_ids)
        self.google = GoogleClients(transport)
        self.telephony = TelephonyClient(transport)
        self.github = GitHubClient(transport)
        self.agents = AgentsClient(transport)
        self.voice = VoiceClient(transport)
        self.camera = CameraClient(transport)
        self._transport = transport

    def model_endpoint(self) -> ModelEndpoint:
        """The OpenAI-compatible endpoint for this run's granted model profiles."""
        return ModelEndpoint.for_transport(self._transport)
