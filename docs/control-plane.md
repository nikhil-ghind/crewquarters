# Control plane developer guide

Owner: Nikhil Hiro Ghind (Person 1). This guide covers the control API, the scheduler/worker, the database schema, and the contracts other services integrate with.

## Layout

| Path | Package | Contents |
| --- | --- | --- |
| `packages/shared_python` | `crewquarters_shared` | Config, UUIDv7, errors, redaction, ORM models, job queue (`jobs.py`), run lifecycle (`runs/`), cron (`cron.py`), manifest validation, capability tokens, runtime adapter contract, fakes |
| `services/control_api` | `crewquarters_api` | FastAPI app, auth, routers, idempotency, catalog sync, OpenAPI rendering, `cq-admin`, Alembic migrations |
| `services/scheduler` | `crewquarters_scheduler` | Job worker, scheduler tick, reconciler, leader election (`cq-scheduler`) |
| `packages/contracts` | — | OpenAPI (generated), manifest schema, capability vocabulary, event schema, generated clients |
| `catalog/dev` | — | Development agent manifests loaded when `CQ_CATALOG_DIR` points here |

## Running

```bash
make db-up          # PostgreSQL 16 + pgvector on 127.0.0.1:55432
make dev-up         # postgres, migrate, control-api (:8080), scheduler with fake runtime
make test-platform  # control-plane unit + integration tests against a throwaway database
make contracts      # regenerate openapi.yaml and clients; CI fails on any diff
make lint           # ruff + mypy
```

To create the owner: `docker compose -f infra/compose/compose.yaml exec control-api cq-admin bootstrap-token`, then `POST /api/v1/bootstrap` with that code. The OpenAPI UI is at `/api/v1/docs`.

## Fake runtime and the dev agent

`catalog/dev/hello-crew.yaml` is a development agent: manual and schedule triggers, `local.general`, user input. Its image digest is a placeholder. With `CQ_RUNTIME_ADAPTER=fake`, the worker uses `crewquarters_shared/fakes/runtime.py`, which simulates the SDK protocol (handshake, heartbeats, progress events, result) by calling the run service directly.

The installation config field `fakeScenario` selects the behavior:

| Scenario | Behavior |
| --- | --- |
| `succeed` (default) | Progress events, then `SUCCEEDED` |
| `ask` | Asks input key `confirm` (`decision`: continue/cancel), waits, then succeeds |
| `fail` | Reports a retryable `FAKE_FAILURE` |
| `hang` | Never hand shakes; the reconciler interrupts it when the lease expires |
| `crash` | Exits before the handshake; the run becomes `INTERRUPTED` (`HEARTBEAT_LOST`) |
| `slow` | Runs for an hour, heartbeating; use it to test cancellation and active timeouts |
| `model` | Enters `LOADING_MODEL` then returns to `RUNNING` |

`fakeStepSeconds` (default 0.2) sets the delay between steps.

## Integration points for other owners

### Capability broker and SDK: `/internal/v1`

Every call needs `Authorization: Bearer $CQ_INTERNAL_SERVICE_TOKEN`. The broker first verifies the agent's capability token, then calls the route and passes the token's `att` (attempt) in the body. Calls from a stale attempt return `409 STALE_ATTEMPT`.

| Route | Purpose |
| --- | --- |
| `GET /internal/v1/runs/{runId}` | Run state, current attempt, cancel flag, permission snapshot, model bindings, config (for capability checks) |
| `POST /internal/v1/runs/{runId}/handshake` | SDK handshake: `PREPARING` → `RUNNING` |
| `POST /internal/v1/runs/{runId}/heartbeat` | Extend the attempt heartbeat lease; returns `cancelRequested` |
| `POST /internal/v1/runs/{runId}/events` | Append `run.log`, `run.progress`, `run.metric`, or `run.artifact` (redacted) |
| `POST /internal/v1/runs/{runId}/model-state` | Model gateway: `RUNNING` ↔ `LOADING_MODEL` |
| `POST /internal/v1/runs/{runId}/result` | Final result, `succeeded` or `failed`; the first result wins |
| `POST /internal/v1/runs/{runId}/input-requests` | `ctx.input.ask`: create or return the request for a stable key |
| `GET /internal/v1/input-requests/{id}?wait=N` | Long-poll (≤ 30 s) until the request is answered, cancelled, or expired |
| `POST /internal/v1/runs/{runId}/actions/{key}/claim` | `ctx.idempotency`: returns `claimed` (only to the call that created the key), `completed` (with result), or `in_doubt` (claimed before and never completed: check the provider first) |
| `POST /internal/v1/runs/{runId}/actions/{key}/complete` | Record an action's result |

### Model gateway

The gateway (Akshay Sunil Navani, Person 2) calls `POST /internal/v1/runs/{runId}/model-state` with `{"attempt", "loading": true, "model"}` while a lease is loading, and `loading: false` when the model is ready. It verifies capability tokens and checks for `llm.profile:<variant>` and `cloud.<provider>`.

### Runtime daemon

The daemon (Akshay Sunil Navani, Person 2) implements the contract used by `DaemonRuntimeClient` in `crewquarters_shared/runtime.py`, over its Unix socket with the bearer service token:

- `POST /internal/v1/runs` with a `RunSpec` body → `{"runtimeRef": "..."}`. This **must be idempotent on `(run_id, attempt)`**: a repeated call returns the same reference and never starts a second container.
- `GET /internal/v1/runs/{runtimeRef}` → `{"state": "starting|running|exited", "exitCode"}`; `404` means missing.
- `POST /internal/v1/runs/{runtimeRef}/cancel` with `{"graceSeconds"}`: SIGTERM, then kill.
- `GET /internal/v1/host/capacity` returns architecture, memory, disk, and GPU/runtime checks.

The `RunSpec` carries image@digest, entrypoint, architectures, cpu, memory_mb, pids, config (mounted read-only), and env: `PLATFORM_BROKER_URL`, `PLATFORM_RUN_TOKEN`, `PLATFORM_RUN_ID`, `PLATFORM_ATTEMPT`.

### Capability tokens

The worker mints a token when an attempt starts (`crewquarters_shared/capability.py`). It is HS256-signed with `CQ_CAPABILITY_SIGNING_KEY`, with audience `crewquarters-broker` and issuer `crewquarters-control-api`. Claims:

| Claim | Contents |
| --- | --- |
| `jti` | Token ID |
| `sub` | `run:<id>` |
| `iat`, `exp` | Issue and expiry times. Lifetime = prepare timeout + `activeTimeoutSeconds` + `maxInputWaitSeconds` + 300 s, so a token never expires during a legitimate attempt; the broker still rejects it once the run is no longer active or the attempt is not current |
| `run`, `att`, `ins`, `ver` | Run, attempt, installation, and agent version IDs |
| `cap` | Sorted capability strings from the approved permissions (see `packages/contracts/capabilities.yaml`) |
| `res` | Includes `modelBindings` |

The broker and gateway verify tokens with `crewquarters_shared.capability.verify(token, key)`. They must also check the current run state through `GET /internal/v1/runs/{id}`: an active state and a matching `currentAttempt`.

### Status clients

The public `/api/v1/models*` and `/api/v1/connections` endpoints read through the `ModelStatusClient` and `ConnectionStatusClient` protocols in `crewquarters_shared/clients.py`. They currently use **fakes** (`FakeModelStatusClient`, `FakeConnectionStatusClient`). Akshay Sunil Navani (Person 2) and Nikhil Sajan Khaneja (Person 3) add HTTP implementations when their internal APIs freeze. Installation readiness uses the same clients.

## Database ownership

Table ownership follows PLAN.md section 6.1. Each owner designs their tables and access modules. **Nikhil Hiro Ghind (Person 1) reviews and merges every migration** so that Alembic keeps one linear history. The `jobs` table is shared, and is accessed only through `crewquarters_shared/jobs.py`.

Control-plane tables added beyond the PLAN list:

- `idempotency_records`: HTTP `Idempotency-Key` replay;
- `idempotency_actions`: agent action keys.

The database itself enforces two rules: `agent_versions` rows are immutable, and `audit_events` rows are append-only.

## API conventions (for the web UI and other clients)

- **Pagination:** every list endpoint returns `{items, nextCursor}`. Pass `cursor=<nextCursor>` and `limit` (1–200) to get the next page. The run event history uses the event sequence instead (`after=`).
- **Idempotency:** every authenticated `POST`, `PATCH` and `DELETE` accepts `Idempotency-Key` (8–200 characters). A retry with the same key and body replays the stored response with `Idempotent-Replayed: true`. The same key with a different body returns `409 IDEMPOTENCY_KEY_REUSED`. `/bootstrap` and `/sessions` are exempt: they have no user yet, and they are rate limited, and bootstrap works only once.
- **Body size:** requests over `CQ_MAX_BODY_BYTES` (2 MiB) get `413 PAYLOAD_TOO_LARGE`.
- **Attention:** `GET /api/v1/attention` returns the Activity badge count and the dashboard's "Needs attention" items:
  - unanswered Crew Requests;
  - failed or interrupted runs the owner hasn't acknowledged (`POST /api/v1/runs/{id}/acknowledge`);
  - enabled schedules whose agent is not ready;
  - models whose download or load failed.
- **Setup wizard:** `PATCH /api/v1/settings` with `setupState` stores wizard progress on the server, so setup resumes after a refresh or an OAuth redirect. `setupCompleted` hides the wizard.
- **Schedules** include `ready` and `blockers` (the failing readiness checks), so the UI can show whether required connections and models are ready.

## Observability

- **Logs:** both services write one JSON object per line. Each line has `ts`, `level`, `service`, `logger` and `message`, plus `request_id`, `run_id`, `event`, `job_id` and `job_type` when known. Messages and fields are redacted: tokens, secret-looking keys and phone numbers. The access log records route templates only, never raw paths or query strings. Set `CQ_LOG_FORMAT=text` for readable local output.
- **Control API metrics:** `GET /internal/v1/metrics` (service token), in Prometheus text format:
  - HTTP requests and latency by route template;
  - jobs by state and type, and the age of the oldest waiting job;
  - runs by state and pending Crew Requests;
  - the latest schedule dispatch lag.
- **Scheduler metrics:** `GET /metrics` and `GET /health` on `CQ_SCHEDULER_METRICS_HOST:CQ_SCHEDULER_METRICS_PORT` (default `127.0.0.1:9101`). They cover leadership, ticks, schedule outcomes, jobs processed by type and outcome, and reconciler actions.
- **System status:** `GET /api/v1/system/status` includes GPU and NVIDIA runtime checks from the runtime daemon's `/internal/v1/host/capacity` when `CQ_RUNTIME_ADAPTER=daemon`.

## Security notes for integrators

- The internal run view (`GET /internal/v1/runs/{id}`) includes `capabilityTokenId`, the `jti` of the current attempt's token. The broker must reject any token whose `jti` differs from it, which revokes tokens from earlier attempts.
- `POST /internal/v1/runs/{id}/input-requests` returns `403 PERMISSION_DENIED` unless the run's approved permissions include `userInput`. The control API enforces this in addition to the broker.
