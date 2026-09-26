from typing import Any

from crewquarters import Agent, RunContext
from crewquarters.errors import PlatformError

agent = Agent("worker-agent")


@agent.run
async def run(ctx: RunContext[Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "trigger": ctx.run.trigger,
        "parentRunId": ctx.run.parent_run_id,
        "input": ctx.run.input,
    }
    if ctx.config.get("startBack"):
        try:
            await ctx.agents.start("starter-agent", key="back")
        except PlatformError as exc:
            result["startBackError"] = exc.code
    return result


if __name__ == "__main__":
    agent.serve()
