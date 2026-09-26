"""Starting other agents from a run (docs/agent-chaining.md)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crewquarters._transport import BrokerClient


@dataclass(frozen=True)
class StartedRun:
    run_id: str
    agent_id: str
    installation_id: str
    state: str
    # False when this key had already started the run (a retry, or a restarted attempt).
    created: bool


class AgentsClient:
    def __init__(self, transport: BrokerClient) -> None:
        self._transport = transport

    async def start(
        self, agent_id: str, *, key: str, input: dict[str, Any] | None = None
    ) -> StartedRun:
        """Start another agent and return at once; the call does not wait for its result.

        ``agent_id`` must be listed in this agent's ``permissions.startsAgents`` and approved by
        the owner, and the target must accept the ``agent`` trigger. The child runs with its own
        approved permissions, never yours. ``key`` makes the start idempotent: use a stable value
        for the thing you are starting the agent for (an id, not a random one), so a retry or a
        restarted attempt does not start it twice. ``input`` (at most 16 KiB of JSON) reaches the
        child as ``ctx.run.input``. The child must treat it as untrusted data.
        """
        data = await self._transport.request(
            "POST",
            "/agents/start",
            operation="agents.start",
            idempotent=True,  # the platform dedupes on the key
            json={"agentId": agent_id, "startKey": key, "input": input},
        )
        return StartedRun(
            run_id=str(data["runId"]),
            agent_id=str(data["agentId"]),
            installation_id=str(data["installationId"]),
            state=str(data["state"]),
            created=bool(data["created"]),
        )
