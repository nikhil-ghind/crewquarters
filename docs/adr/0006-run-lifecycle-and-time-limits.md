# 0006. Run lifecycle, attempts, and time limits

- Status: Accepted
- Date: 2026-09-24
- Owner: Nikhil Hiro Ghind (Person 1)

## Context

Runs must survive worker crashes and platform restarts, wait up to 24 hours for human input, and never repeat external side effects silently. PLAN.md section 7.2 defines the state machine, and fix 1 in the plan separates active time from waiting time.

## Decision

**State machine** (`crewquarters_shared/runs/states.py`, enforced by `runs/service.py:transition`):

```mermaid
stateDiagram-v2
    [*] --> QUEUED
    QUEUED --> PREPARING
    QUEUED --> CANCELLED
    PREPARING --> RUNNING
    PREPARING --> FAILED
    PREPARING --> CANCELLING
    PREPARING --> INTERRUPTED
    RUNNING --> LOADING_MODEL
    RUNNING --> WAITING_INPUT
    RUNNING --> SUCCEEDED
    RUNNING --> FAILED
    RUNNING --> CANCELLING
    RUNNING --> INTERRUPTED
    LOADING_MODEL --> RUNNING
    LOADING_MODEL --> FAILED
    LOADING_MODEL --> CANCELLING
    LOADING_MODEL --> INTERRUPTED
    WAITING_INPUT --> RUNNING
    WAITING_INPUT --> CANCELLING
    WAITING_INPUT --> INTERRUPTED
    WAITING_INPUT --> FAILED
    CANCELLING --> CANCELLED
    INTERRUPTED --> QUEUED: owner retry
    FAILED --> QUEUED: owner retry (retryable only)
```

`SUCCEEDED` and `CANCELLED` are final. Every transition appends a `run.state_changed` event, and the per-run event sequence is serialized by the run row lock.

**Attempts**

- Each start creates a `run_attempts` row, unique per `(run_id, attempt)`.
- Retry increments `current_attempt` and enqueues `run.dispatch` with dedupe key `run:{id}:attempt:{n}`.
- Calls from the broker and SDK name their attempt. Any call from an older attempt fails with `409 STALE_ATTEMPT`.

**Starting containers**

- The worker holds the run row lock while it calls `RuntimeAdapter.start_run`.
- `start_run` must be idempotent on `(run_id, attempt)`. If `runtime_ref` is already set, a reclaimed dispatch job does nothing more.
- A run that still has an active attempt gets exactly one container, even after a worker crash.
- The worker never holds the run row lock during runtime calls. It commits `PREPARING` and the attempt, starts the container unlocked, then re-locks to record the runtime reference. If the run was cancelled or failed in between, it stops the new container.
- Dispatch refuses a disabled, removed, or unapproved installation (`INSTALLATION_NOT_READY`), and an owner retry re-checks readiness.

**Time limits** (manifest `resources`)

- `activeTimeoutSeconds` counts time in `PREPARING`, `LOADING_MODEL`, and `RUNNING`.
- `maxInputWaitSeconds` (at most 86 400) counts time in `WAITING_INPUT`.
- Elapsed time is added to `active_seconds_used` or `input_wait_seconds_used` on every transition.
- The reconciler (`crewquarters_scheduler/reconciler.py`) fails runs with `ACTIVE_TIMEOUT` or `INPUT_TIMEOUT` (not retryable) and enqueues `run.stop`. It checks the wait budget before expiring inputs.
- A single `ctx.input.ask` timeout may not exceed the remaining wait budget (`422 INPUT_WAIT_BUDGET_EXCEEDED`).
- Both limits are per attempt: an owner retry resets both counters.
- The capability token lives for the prepare timeout + both limits + 300 s, so it cannot expire during a legitimate attempt. The broker still rejects it once the run is inactive or the attempt is not current.

**Heartbeats**

- The SDK heartbeats through the broker. Each heartbeat extends `run_attempts.heartbeat_expires_at` by `CQ_HEARTBEAT_TIMEOUT_SECONDS`. Before the handshake, the lease is `CQ_PREPARE_TIMEOUT_SECONDS`.
- An expired lease moves the run to `INTERRUPTED` with `HEARTBEAT_LOST` (retryable), and the container is stopped. This covers hung agents, platform restarts, and (as a backstop) container crashes; see revision 1 for how exits are detected directly.

**Input requests**

- Requests are unique per `(run_id, key)`. A retried attempt that asks the same key gets the existing request: an answer given to an earlier attempt is returned as-is, and a request that an earlier attempt left `cancelled` or `expired` (for example after a restart) is reopened as `pending` with a new version and deadline.
- Agent-supplied input schemas may not use `pattern` or `patternProperties` (catastrophic-backtracking protection) and are limited to 16 KiB. Previews and answers are limited to 64 KiB, and agent events to 16 KiB.
- The model gateway's loading signal only moves `RUNNING` ↔ `LOADING_MODEL`; in any other state it is a no-op, so it cannot pull a run out of `WAITING_INPUT`.
- Answers carry the request `version`. A stale version returns `409 VERSION_CONFLICT` and a closed request returns `409 INPUT_ALREADY_CLOSED`.
- Answers are validated against the request's JSON Schema.
- Expired, cancelled, or answered requests move a `WAITING_INPUT` run back to `RUNNING` once none are left pending.

**Idempotency actions** (`ctx.idempotency`, table `idempotency_actions`, unique per `(run_id, key)`). A claim returns one of three statuses:

- `claimed`: proceed with the action. Only the call that creates the key ever receives this;
- `completed`: returns the stored result;
- `in_doubt`: the key was claimed earlier (by this attempt, for example a retried request whose response was lost, or by an earlier attempt) and never completed, so the action may already have happened. The agent must check the provider's state before acting. The key stays `in_doubt` until completed.

**Lock order.** Code always locks the `agent_runs` row before any `input_requests` row, which prevents deadlocks between answers, cancels, and the reconciler.

## Consequences

- Delivery is at least once, and every side effect is guarded by a key.
- An `INTERRUPTED` run is recovered by an explicit owner retry, not automatically. The UI shows whether external actions already happened.
- Durable suspend/resume of arbitrary Python is still deferred (PLAN.md section 26).

## Revision 1 (2026-09-25): container exits are read from the runtime

**Context.** The real-stack suite (`docs/testing-realstack.md`) found that nothing read a run
container's exit status. A crashed or OOM-killed agent was caught only by the heartbeat lease,
as `INTERRUPTED`/`HEARTBEAT_LOST`, which cannot tell a crash from an out-of-memory kill.
`run_attempts.exit_code` was never written, and a container that died before its handshake
surfaced only after `CQ_PREPARE_TIMEOUT_SECONDS` (600 s by default).

**Decision.**

- The scheduler leader runs an exit watcher (`crewquarters_scheduler/exits.py`) every
  `CQ_EXIT_WATCH_INTERVAL_SECONDS` (2 s). It asks the runtime (`GET /internal/v1/runs/{ref}`,
  which reports `exitCode`, `oomKilled`, `finishedAt` and `memoryLimitBytes`) about every attempt
  that has a container and no exit code: the current attempt of a run in `PREPARING`,
  `LOADING_MODEL`, `RUNNING`, `WAITING_INPUT` or `CANCELLING`, and any attempt that ended in
  the last 10 minutes.
- Runtime calls are made with no transaction open. Only after observing an exit does the
  watcher lock the run (`service.record_container_exit`). It then:
  - writes `run_attempts.exit_code`;
  - if the run is still in `PREPARING`, `LOADING_MODEL`, `RUNNING` or `WAITING_INPUT` on that
    attempt, fails it (`FAILED`, enqueues `run.stop`, cancels open questions) with:

    | Exit | Code | Message / details |
    | --- | --- | --- |
    | `oomKilled`, or exit code 137 (SIGKILL) without it | `AGENT_OUT_OF_MEMORY` | Names the memory limit (the daemon's `memoryLimitBytes`, else the manifest's `resources.memoryMb`); `details: {exitCode, oomKilled, memoryLimitMb}`. Docker loses `OOMKilled` for about 1 in 12 OOM kills (Docker 29, cgroup v2). The platform never SIGKILLs a container whose run is still in these states, so an unflagged 137 is reported as a probable OOM and the message says it was not confirmed. |
    | any other non-zero | `AGENT_EXITED` | The exit code, and "before its handshake" in `PREPARING`; `details: {exitCode, oomKilled: false}` |
    | 0 | `AGENT_EXITED_WITHOUT_RESULT` | `details: {exitCode: 0, oomKilled: false}` |

  - otherwise (the run is already `SUCCEEDED`, `FAILED`, `CANCELLED`, `INTERRUPTED`,
    `CANCELLING`, or on a newer attempt) it changes nothing else.
- **Result versus exit.** The SDK posts its result and waits for the answer before its process
  exits, so a result is committed before its container's exit can be observed. Because the
  watcher observes first and locks second, a run whose agent posted a result and then exited
  stays `SUCCEEDED`, and only its exit code is recorded.
- **Retry.** All three codes are retryable, as `HEARTBEAT_LOST` was for the same failures: the
  owner decides whether to retry. `ACTIVE_TIMEOUT` and `INPUT_TIMEOUT` stay non-retryable.
- A container the runtime no longer knows (`missing`) and a runtime that does not answer are
  left to the heartbeat lease, which remains the backstop for hung agents and lost hosts.
- **SDK outages.** The SDK retries broker outages for 2.5 heartbeat intervals from the first
  failure (25 s by default), so a broker restart shorter than the heartbeat timeout no longer
  fails the agent's in-flight call. Only requests that were never sent, or idempotent ones,
  are retried (`docs/sdk/reference.md`).

**Consequences.** A crash surfaces in about 2 s, with its exit code and an actionable error,
instead of after the lease. `INTERRUPTED`/`HEARTBEAT_LOST` now means the agent stopped
answering while its container kept running, or the platform lost track of it. The fake
runtime's `crash`, `oom` and `exit0` scenarios exercise the watcher in unit tests.
