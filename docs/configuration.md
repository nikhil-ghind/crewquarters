# Configuration

The Python services read `CQ_*` environment variables through `crewquarters_shared/config.py` (pydantic-settings). List values (`CQ_PUBLIC_ORIGINS`, `CQ_FAKE_CONNECTIONS`) are JSON arrays, for example `["https://crewquarters.local"]`.

> **Secret defaults are insecure.** The defaults for `CQ_SECRET_KEY`, `CQ_CAPABILITY_SIGNING_KEY`, and `CQ_INTERNAL_SERVICE_TOKEN` exist only so the `dev` profile starts. The installer (`demo-cpu`, `dgx`) must generate a random value of at least 256 bits for each, store it with mode `0600`, and never commit it.

Profiles: **dev** = laptop Compose with fakes; **demo-cpu** = laptop end-to-end; **dgx** = GB10 appliance. "Set by" lists the profiles where the value is normally changed from its default.

| Variable | Type | Default | Secret | Set by | Purpose |
| --- | --- | --- | --- | --- | --- |
| `CQ_PROFILE` | string | `dev` | no | all | Deployment profile name, shown in system status |
| `CQ_DATABASE_URL` | string | `postgresql+psycopg://crewquarters:crewquarters@127.0.0.1:55432/crewquarters` | yes (contains password) | all | PostgreSQL connection URL |
| `CQ_DB_POOL_SIZE` | int | `10` | no | — | Connection pool size per process |
| `CQ_SECRET_KEY` | secret string | insecure dev value | **yes** | demo-cpu, dgx | HMAC key for CSRF tokens |
| `CQ_CAPABILITY_SIGNING_KEY` | secret string | insecure dev value | **yes** | demo-cpu, dgx | HS256 key for run capability tokens; shared with broker and model gateway |
| `CQ_INTERNAL_SERVICE_TOKEN` | secret string | insecure dev value | **yes** | demo-cpu, dgx | Bearer credential for `/internal/v1` and the runtime daemon socket |
| `CQ_PUBLIC_ORIGINS` | list[string] | `["http://localhost:8080","http://127.0.0.1:8080"]` | no | dgx (LAN mode) | Origins allowed for state-changing requests |
| `CQ_COOKIE_SECURE` | bool | `false` | no | dgx (LAN/HTTPS) | Adds `Secure` to session and CSRF cookies |
| `CQ_SESSION_IDLE_SECONDS` | int | `43200` (12 h) | no | — | Sliding idle session timeout |
| `CQ_SESSION_ABSOLUTE_SECONDS` | int | `604800` (7 d) | no | — | Absolute session lifetime |
| `CQ_AUTH_RATE_LIMIT_PER_MINUTE` | int | `10` | no | — | Bootstrap/login attempts per IP per minute |
| `CQ_CONTRACTS_DIR` | path | `packages/contracts` (repo-relative) | no | container images | Location of the manifest JSON Schema |
| `CQ_CATALOG_DIR` | path | unset | no | all | Bundled agent manifests loaded at API startup |
| `CQ_RUNTIME_ADAPTER` | `fake` \| `daemon` | `fake` | no | dgx (`daemon`) | Worker runtime backend |
| `CQ_RUNTIME_SOCKET` | path | `/run/crewquarters/runtime.sock` | no | dgx | Runtime daemon Unix socket |
| `CQ_MODEL_GATEWAY_ADAPTER` | string | `fake` | no | dev | Model status client. Only `fake` exists today; any other value stops the API at startup until Akshay Sunil Navani (Person 2) adds the HTTP client |
| `CQ_BROKER_ADAPTER` | string | `fake` | no | dev | Connection status client. Only `fake` exists today; any other value stops the API at startup until Nikhil Sajan Khaneja (Person 3) adds the HTTP client |
| `CQ_FAKE_CONNECTIONS` | list[string] | `["google","twilio","openai","anthropic"]` | no | dev | Providers the fake connection client reports as connected |
| `CQ_BROKER_URL` | string | `http://capability-broker:8000` | no | all | Passed to agents as `PLATFORM_BROKER_URL` |
| `CQ_HEARTBEAT_TIMEOUT_SECONDS` | int | `30` | no | — | Attempt heartbeat lease after handshake |
| `CQ_PREPARE_TIMEOUT_SECONDS` | int | `600` | no | — | Lease before handshake (image pull, container start) |
| `CQ_SCHEDULER_TICK_SECONDS` | float | `1.0` | no | — | Scheduler evaluation interval |
| `CQ_MISFIRE_GRACE_SECONDS` | int | `60` | no | — | Lateness after which a schedule counts as misfired |
| `CQ_JOB_LEASE_SECONDS` | int | `30` | no | — | Job lease; also the worst-case recovery time after worker death |
| `CQ_WORKER_CONCURRENCY` | int | `4` | no | — | Concurrent job loops per scheduler process |
| `CQ_RECONCILER_INTERVAL_SECONDS` | float | `2.0` | no | — | Reconciler interval on the leader |
| `CQ_API_HOST` | string | `127.0.0.1` | no | Compose (`0.0.0.0`) | Control API bind address (read in `crewquarters_api/main.py`) |
| `CQ_API_PORT` | int | `8080` | no | — | Control API port |
| `CQ_TEST_ADMIN_URL` | string | dev Compose database URL | no | tests/CI | PostgreSQL server where tests create throwaway databases |
| `CQ_MAX_BODY_BYTES` | int | `2097152` | no | all | Largest accepted request body; larger bodies get `413 PAYLOAD_TOO_LARGE` |
| `CQ_SCHEDULER_METRICS_HOST` | string | `127.0.0.1` | no | all (`0.0.0.0` inside Compose) | Bind address of the scheduler's `/metrics` and `/health` endpoint |
| `CQ_SCHEDULER_METRICS_PORT` | int | `9101` | no | all | Port of the scheduler's `/metrics` and `/health` endpoint |
| `CQ_LOG_FORMAT` | string | `json` | no | all | `json` (structured, redacted) or `text` for local reading |
| `CQ_LOG_LEVEL` | string | `INFO` | no | all | Root log level |
| `CQ_MODEL_GATEWAY_URL` | string | `http://model-gateway:8090` | no | all | Model gateway base URL used by the control API |
| `CQ_GATEWAY_RUNTIME` | string | `daemon` | no | dev: `inprocess` | `daemon` (model servers via the runtime daemon) or `inprocess` (mock, no Docker) |
| `CQ_GATEWAY_RUNTIME_SOCKET` | path | `/run/crewquarters/runtime.sock` | no | all | Runtime daemon socket |
| `CQ_GATEWAY_MODEL_ADDRESSING` | string | `name` | no | all | Reach model containers by name (Compose) or `ip` (host-side gateway, tests) |
| `CQ_GATEWAY_CATALOG_DIR` | path | repo `catalog/models/dev` | no | dgx: `/usr/share/crewquarters/catalog/models` | Model profile JSON directory |
| `CQ_GATEWAY_CONTROL_API_URL` | string | `http://control-api:8080` | no | all | Control API for run checks and `LOADING_MODEL` signals |
| `CQ_GATEWAY_SYSTEM_RESERVE_BYTES` | int | 24 GiB | no | dev: 2 GiB | Memory kept free for the OS, database, agents |
| `CQ_GATEWAY_MAX_SERVING_BYTES` | int | 96 GiB | no | dev: 8 GiB | Cap on total model reservations |
| `CQ_GATEWAY_LOAD_SAFETY_MARGIN_BYTES` | int | 8 GiB | no | dev: 1 GiB | Extra margin per load |
| `CQ_GATEWAY_ONE_GENERATIVE_MODEL` | bool | `true` | no | all | Allow only one resident generative model |
| `CQ_GATEWAY_IDLE_UNLOAD_SECONDS` | int | `600` | no | all | Idle grace before unloading |
| `CQ_GATEWAY_RUN_LEASE_TTL_SECONDS` / `CQ_GATEWAY_CHAT_LEASE_TTL_SECONDS` | int | `300` / `43200` | no | all | Lease lifetimes |
| `CQ_GATEWAY_PER_RUN_TOKEN_LIMIT` | int | `200000` | no | all | Tokens per run across all calls |
| `CQ_GATEWAY_DAILY_CLOUD_TOKEN_BUDGET` | int | `0` (off) | no | all | Daily cloud tokens per provider |
| `CQ_GATEWAY_ANTHROPIC_DEFAULT_MODEL` | string | `claude-opus-5` | no | all | Model for `anthropic.default` |
| `CQ_GATEWAY_ANTHROPIC_FALLBACKS` | bool | `true` | no | all | Anthropic server-side refusal fallback |
| `CQ_GATEWAY_OPENAI_MODELS` / `CQ_GATEWAY_ANTHROPIC_MODELS` | JSON | `{}` | no | all | Cloud profile name to model id |
| `CQ_GATEWAY_HOST` / `CQ_GATEWAY_PORT` | string / int | `127.0.0.1` / `8090` | no | Compose: `0.0.0.0` | Gateway listen address |
| `CQ_RUNTIME_*` | | | token: yes | host | Runtime daemon settings; see docs/runtime-daemon.md |
