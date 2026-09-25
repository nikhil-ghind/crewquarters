# Agent SDK quickstart

This walks through writing a Crewquarters agent with `crewquarters-sdk` and testing it against the
fake platform with `crewctl`. It works on any laptop, with no GPU, Google account, or Twilio
account.

## 1. Set up

```bash
make sync                      # uv workspace, Python 3.12, all packages installed
uv run --all-packages crewctl --help
```

## 2. Scaffold

```bash
uv run --all-packages crewctl init weather-bot --dir agents/weather_bot
```

This creates:

| File | Purpose |
| --- | --- |
| `manifest.yaml` | Identity, permissions, resources, configuration schema. The image is `…@sha256:REQUIRED_DIGEST` until you build. |
| `src/weather_bot/__main__.py` | The agent |
| `Dockerfile` | Non-root (10001), read-only-root compatible, pinned `python:3.12-slim` base |
| `scenarios/default/` | Fake-platform scenario (`scenario.yaml`) and install configuration (`config.yaml`) |
| `tests/test_agent.py` | Runs the default scenario |

## 3. Write the agent

```python
from typing import Any

from crewquarters import Agent, RunContext

agent = Agent("weather-bot")


@agent.run
async def run(ctx: RunContext[Any]) -> dict[str, Any]:
    await ctx.events.progress(10, "Starting")
    answer = await ctx.input.ask(
        "confirm-v1", "Continue?", "Should the agent continue?", choices=["yes", "no"], timeout_seconds=600
    )
    reply = await ctx.llm.chat("local.general", [{"role": "user", "content": "Say hello in five words."}])
    return {"confirmed": answer.value == "yes", "greeting": reply.text}


if __name__ == "__main__":
    agent.serve()
```

- Declare every capability you use in `manifest.yaml` (`permissions`). The broker enforces them, so
  an undeclared call raises `PermissionDenied`.
- Pass `config_model=` and `result_model=` (Pydantic models) to validate configuration and results.
  An invalid configuration fails the run with `CONFIG_INVALID` before your code runs.
- The full API is in [reference.md](reference.md). Also read [idempotency.md](idempotency.md) before
  your agent does anything with an external effect, and [untrusted-content.md](untrusted-content.md)
  before you put emails or documents into a prompt.

## 4. Validate and test

```bash
uv run --all-packages crewctl validate agents/weather_bot --allow-unbuilt
uv run --all-packages crewctl test agents/weather_bot                 # prints the event timeline and result
uv run --all-packages crewctl test agents/weather_bot --json          # machine-readable
```

`crewctl test` starts an in-process fake platform, loads `scenarios/<name>`, installs the agent with
every requested permission approved, runs it as a local process, and exits 0 only if the run
`SUCCEEDED`.

A scenario can seed Gmail, Sheets, Twilio outcomes, mock-LLM rules, knowledge documents, and
automatic answers. See `tests/fixtures/scenarios/*` and spec §6.4 for the formats. To run on a
schedule, add `run: {trigger: schedule, scheduledFor: now}` to `scenario.yaml`.

## 5. Build, pin, publish

```bash
make dev-up                                                    # fake platform + local registry
uv run --all-packages crewctl build agents/weather_bot --push  # amd64 + arm64; pins the digest in manifest.yaml
uv run --all-packages crewctl test agents/weather_bot --docker # pinned image, hardened container, internal network
uv run --all-packages crewctl publish agents/weather_bot --target local --platform-url http://localhost:8080
```

Pass `--output-manifest PATH` to `crewctl build` to write the pinned manifest somewhere else, for
example in CI.

The hardened container has: user `10001:10001`, a read-only root, a 64 MiB `/tmp` tmpfs, all Linux
capabilities dropped, `no-new-privileges`, PID/CPU/memory limits, and a network with no route
anywhere except the broker. If your agent works in `--docker` mode, it will work under the real
runtime daemon.
