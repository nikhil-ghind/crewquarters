# Agents starting agents

A running agent can start another agent. It is a permission the owner approves, not something an agent
can do on its own, and the platform enforces every limit in one place (the control API's `chaining.py`).

## Using it (SDK)

The starting agent lists the agents it may start in its manifest, and the target lists the `agent` trigger:

```yaml
# starter: manifest.yaml
spec:
  triggers: ["manual", "schedule"]
  permissions:
    startsAgents: ["notifier"]     # agent ids; at most 10; never your own id
# notifier: manifest.yaml
spec:
  triggers: ["manual", "agent"]    # "agent" = another agent may start it
```

```python
started = await ctx.agents.start(
    "notifier",
    key=f"pr-{pull.number}",           # stable, so a retry or restarted attempt starts it once
    input={"pr": pull.number, "bugs": 2},   # at most 16 KiB of JSON
)
started.run_id, started.created         # created is False when the key had already started it
```

The call returns at once; it does not wait for the child, and there is no way to read the child's result.
Hand data back through a shared place (a Sheet, a GitHub comment) instead.

The child sees how it was started:

```python
ctx.run.trigger        # "agent"
ctx.run.parent_run_id  # the run that started it
ctx.run.input          # what the caller passed: UNTRUSTED
```

`ctx.run.input` was written by another agent, possibly from data it read from the outside. Treat it
like an email body: put it in an evidence block, never in the system prompt
([untrusted-content.md](sdk/untrusted-content.md)).

## What the owner controls

| Control | Where | Effect |
| --- | --- | --- |
| Approval | The install dialog lists "Start the *notifier* agent" | Only approved targets can be started. A new agent version that asks for more needs re-approval, like any permission |
| Target installation | Owner's installations | The target must be one of the owner's own installations, enabled and ready. The child runs with **its own** approved permissions, never the caller's |
| Which one | `agentTargets` in the caller's installation config, e.g. `{"notifier": "<installation id>"}` | Only needed when the same agent is installed more than once; otherwise the start fails with `NEEDS_CONFIGURATION` |
| Kill switch | `CQ_AGENT_STARTS_ENABLED=false` | Every start is refused with `AGENT_STARTS_DISABLED`, whatever was approved |
| Depth | `CQ_AGENT_CHAIN_MAX_DEPTH` (default 3, max 10) | A manual run is depth 0; its child is 1. Deeper starts are refused with `CHAIN_TOO_DEEP` |
| Count | `CQ_AGENT_STARTS_PER_RUN` (default 5, max 50) | One run may start at most this many runs (`START_LIMIT_REACHED`) |

A chain never revisits an agent: A starts B starts A is refused with `CHAIN_CYCLE`. The Activity page
shows an agent-started run as "Started by another agent", and the audit log records who started whom
(`run.created` with `trigger: agent` and `parentRunId`).

## Errors

All are typed `PlatformError`s with a `code`. A refusal does not end the run; catch it and carry on.

| Code | Meaning |
| --- | --- |
| `CAPABILITY_DENIED` / `PERMISSION_DENIED` | The target was not approved for this installation |
| `AGENT_STARTS_DISABLED` | The owner turned the feature off (403) |
| `TARGET_NOT_INSTALLED` | Not installed, or disabled |
| `TRIGGER_NOT_SUPPORTED` | The target does not list the `agent` trigger |
| `INSTALLATION_NOT_READY` | The target needs a connection, a model, or configuration (`checks` says which) |
| `NEEDS_CONFIGURATION` | Installed more than once; set `agentTargets.<id>` |
| `CHAIN_CYCLE`, `CHAIN_TOO_DEEP`, `START_LIMIT_REACHED` | The platform limits above |
| `START_KEY_REUSED` | The same key already started a different agent |
| `INPUT_TOO_LARGE` (422) | More than 16 KiB of input |

## How it works

`ctx.agents.start` calls the broker's `POST /internal/v1/sdk/agents/start`. The broker checks the
`agents.start:<id>` capability (from the approved permissions) and calls the control API's
`POST /internal/v1/runs/{runId}/agent-runs` with the attempt from the token. The control API applies the
rules above in one transaction and creates a normal `QUEUED` run with `trigger = agent`, `parent_run_id`,
the caller's `start_key` (unique with the parent, which makes the start idempotent) and the input. From
there it is dispatched like any other run.

## Testing with the fake platform

The fake platform applies the same rules (tunable with `CREWQ_FAKE_AGENT_STARTS`,
`CREWQ_FAKE_AGENT_CHAIN_MAX_DEPTH`, `CREWQ_FAKE_AGENT_STARTS_PER_RUN`). A started run stays `QUEUED` until
something dispatches it; in a test, run it by id:

```python
run_agent(client, launcher, None, run_id=child_id)
```

`tests/integration/test_agent_chaining.py` and the two small agents in `tests/integration/agents/`
(`starter_agent`, `worker_agent`) are a complete example.
