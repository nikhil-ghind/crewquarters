from typing import Any

from crewquarters import Agent, RunContext
from crewquarters.errors import PlatformError

agent = Agent("starter-agent")


@agent.run
async def run(ctx: RunContext[Any]) -> dict[str, Any]:
    """Start every run listed in ``config.starts``; report each outcome instead of failing."""
    outcomes: list[dict[str, Any]] = []
    for item in ctx.config["starts"]:
        try:
            started = await ctx.agents.start(
                item["agentId"], key=item["key"], input=item.get("input")
            )
        except PlatformError as exc:
            outcomes.append({"agentId": item["agentId"], "key": item["key"], "error": exc.code})
        else:
            outcomes.append(
                {
                    "agentId": started.agent_id,
                    "key": item["key"],
                    "runId": started.run_id,
                    "created": started.created,
                }
            )
    return {"starts": outcomes, "starts_agents": list(ctx.grants.starts_agents)}


if __name__ == "__main__":
    agent.serve()
