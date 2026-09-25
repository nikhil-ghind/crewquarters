from typing import Any

from crewquarters import Agent, RunContext

agent = Agent("hello-agent")


@agent.run
async def run(ctx: RunContext[Any]) -> dict[str, Any]:
    await ctx.events.progress(10, "starting")
    answer = await ctx.input.ask(
        "greeting-v1",
        "Say hello?",
        "Should the agent say hello?",
        choices=["yes", "no"],
        timeout_seconds=60,
    )
    await ctx.events.log("info", "answer received", answer=answer.value)
    return {
        "said": ctx.config["greeting"] if answer.value == "yes" else None,
        "trigger": ctx.run.trigger,
        "attempt": ctx.run.attempt,
    }


if __name__ == "__main__":
    agent.serve()
