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
| `CQ_MODEL_GATEWAY_ADAPTER` | string | `http` | no | tests: `fake` | Model status and chat client: `http` (the model gateway) or `fake` (control-API unit tests only) |
| `CQ_BROKER_ADAPTER` | `http` \| `fake` | unset: `fake` in `dev`, `http` otherwise | no | demo-cpu, dgx: leave unset | Connection status client. `http` reads `GET /internal/v1/connections` from the capability broker; when the broker is unreachable every provider reports `UNKNOWN`, never `CONNECTED`. `fake` reports the `CQ_FAKE_CONNECTIONS` providers as connected and is **refused outside the `dev` profile** (the API does not start) |
| `CQ_FAKE_CONNECTIONS` | list[string] | `["google","twilio","openai","anthropic"]` | no | dev | Providers the fake connection client reports as connected |
| `CQ_BROKER_URL` | string | `http://capability-broker:8000` | no | all | Capability broker base URL: the control API's connection status and management calls (`/internal/v1/connections*`, `/internal/v1/provider-profiles*`), and passed to agents as `PLATFORM_BROKER_URL` |
| `CQ_KNOWLEDGE_URL` | string | `http://knowledge:8000` | no | all | Knowledge service base URL: the control API's knowledge-base, document, retrieval and grounded-chat calls. The broker reads the same variable |
| `CQ_PUBLIC_BASE_URL` | URL | `http://localhost:8080` | no | demo-cpu, dgx (the HTTPS demo hostname) | The single callback origin. The broker builds the Google redirect URI (`…/api/v1/connections/google/callback`) and Twilio callback URLs (`…/api/v1/callbacks/twilio/*`) from it; the control API reports it read-only as `callbackBaseUrl` in `GET /api/v1/settings` (ADR 0009). Set the same value on the control API and the broker |
| `CQ_MAX_UPLOAD_BYTES` | int | `26214400` (25 MiB) | no | all | Per-document upload limit. The control API applies it (plus 64 KiB of multipart framing) to `POST /api/v1/knowledge-bases/{id}/documents` instead of `CQ_MAX_BODY_BYTES`; the knowledge service enforces the same variable. Set it on both, and allow at least this body size at the reverse proxy for that path |
| `CQ_HEARTBEAT_TIMEOUT_SECONDS` | int | `30` | no | — | Attempt heartbeat lease after handshake |
| `CQ_PREPARE_TIMEOUT_SECONDS` | int | `600` | no | — | Lease before handshake (image pull, container start) |
| `CQ_SCHEDULER_TICK_SECONDS` | float | `1.0` | no | — | Scheduler evaluation interval |
| `CQ_MISFIRE_GRACE_SECONDS` | int | `60` | no | — | Lateness after which a schedule counts as misfired |
| `CQ_JOB_LEASE_SECONDS` | int | `30` | no | — | Job lease; also the worst-case recovery time after worker death |
| `CQ_WORKER_CONCURRENCY` | int | `4` | no | — | Concurrent job loops per scheduler process |
| `CQ_RECONCILER_INTERVAL_SECONDS` | float | `2.0` | no | — | Reconciler interval on the leader |
| `CQ_API_HOST` | string | `127.0.0.1` | no | Compose (`0.0.0.0`) | Control API bind address (read in `crewquarters_api/main.py`) |
| `CQ_API_FORWARDED_ALLOW_IPS` | string (IPs/CIDRs or `*`) | `127.0.0.1` | no | Compose (`*`) | Peers whose `X-Forwarded-*` headers the control API trusts (client IP for login rate limits and audit). Compose sets `*` because the API port is unpublished and only the proxy and platform services reach it |
| `CQ_API_PORT` | int | `8080` | no | — | Control API port |
| `CQ_TEST_ADMIN_URL` | string | dev Compose database URL | no | tests/CI | PostgreSQL server where tests create throwaway databases |
| `CQ_MAX_BODY_BYTES` | int | `2097152` | no | all | Largest accepted request body; larger bodies get `413 PAYLOAD_TOO_LARGE`. Document uploads use `CQ_MAX_UPLOAD_BYTES` instead |
| `CQ_SCHEDULER_METRICS_HOST` | string | `127.0.0.1` | no | all (`0.0.0.0` inside Compose) | Bind address of the scheduler's `/metrics` and `/health` endpoint |
| `CQ_SCHEDULER_METRICS_PORT` | int | `9101` | no | all | Port of the scheduler's `/metrics` and `/health` endpoint |
| `CQ_LOG_FORMAT` | string | `json` | no | all | `json` (structured, redacted) or `text` for local reading |
| `CQ_LOG_LEVEL` | string | `INFO` | no | all | Root log level |
| `CQ_MODEL_GATEWAY_URL` | string | `http://model-gateway:8090` | no | all | Model gateway base URL used by the control API (model status and actions, chat, and provider-key tests `POST /internal/v1/provider-profiles/{id}/test`) |
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
| `CQ_GATEWAY_OPENAI_MODELS` / `CQ_GATEWAY_ANTHROPIC_MODELS` | JSON | `{}` | no | all | Operator override: cloud profile name to model ID. Otherwise the provider profile's `allowedModels` apply (docs/model-gateway.md) |
| `CQ_GATEWAY_HOST` / `CQ_GATEWAY_PORT` | string / int | `127.0.0.1` / `8090` | no | Compose: `0.0.0.0` | Gateway listen address |
| `CQ_GATEWAY_WAIT_READY_SECONDS` | int | `900` | no | all | Longest wait for a cold local model; first link of the timeout chain (docs/model-gateway.md, "Timeouts") |
| `CQ_GATEWAY_REQUEST_TIMEOUT_SECONDS` | float | `300` | no | all | One local or cloud model request. Keep wait-ready + request below the broker's 1260 s |
| `CQ_MASTER_KEY_FILE` (gateway) | path | unset (cloud disabled) | **yes** (the file) | demo-cpu, dgx; dev Compose | Device keyring shared with the capability broker; the gateway decrypts OpenAI/Anthropic keys with it. Unset: cloud profiles return `NEEDS_CONNECTION`. Also accepted as `CQ_GATEWAY_MASTER_KEY_FILE` |
| `CQ_GATEWAY_CREDENTIAL_CACHE_SECONDS` | float | `60` | no | all | How long a decrypted provider key stays in memory; `0` disables the cache |
| `CQ_GATEWAY_PROVIDER_TEST_TIMEOUT_SECONDS` | float | `15` | no | all | Timeout of `POST /internal/v1/provider-profiles/{id}/test` provider calls |
| `CQ_GATEWAY_IDEMPOTENCY_TTL_SECONDS` | float | `3600` | no | all | How long a keyed `/llm/chat` result is replayed (in-process store) |
| `CQ_GATEWAY_IDEMPOTENCY_MAX_ENTRIES` | int | `2000` | no | all | Completed keyed results kept in memory |

## Deployment (Compose, proxy, appliance)

Compose interpolation variables, plus the service URLs the stack wires together. The appliance reads these from `/etc/crewquarters/crewquarters.env` (conffile) and `/etc/crewquarters/secrets.env` (generated, 0640 `root:crewquarters`). Proxy routing: docs/runbooks/proxy.md.

| Variable | Type | Default | Secret | Set by | Purpose |
| --- | --- | --- | --- | --- | --- |
| `CQ_KNOWLEDGE_URL` | string | `http://knowledge:8000` | no | all | Knowledge service URL for the control API and the broker |
| `CQ_CONTROL_API_URL` | string | `http://control-api:8080` | no | all | Control API URL for the broker |
| `CQ_PUBLIC_BASE_URL` | URL | `http://localhost:8080` | no | dgx (tunnel) | Public origin of the proxy; the broker builds the Google redirect URI and Twilio callback URLs from it. With a callback tunnel, the tunnel's `https://` hostname |
| `CQ_HTTP_PORT` | int | `8080` | no | all | Host port the proxy publishes (the only published HTTP port) |
| `CQ_BIND_ADDRESS` | string | `127.0.0.1` | no | dgx (LAN mode) | Host address the proxy publishes on (appliance) |
| `CQ_POSTGRES_PORT` | int | `55432` | no | dev | Host port of the dev database (laptop Compose only) |
| `CQ_PLATFORM_IMAGE` | string | dev: `crewquarters/platform:dev`; appliance: `crewquarters/platform` (tag `CQ_VERSION`) | no | all | Platform image |
| `CQ_PROXY_IMAGE` | string | dev: `crewquarters/proxy:dev`; appliance: `crewquarters/proxy` (tag `CQ_VERSION`) | no | all | Edge proxy image (nginx + web UI, `infra/docker/proxy.Dockerfile`) |
| `CQ_KEY_GID` | int | `10500` | no | dev | Group that owns the dev keyring (`root:CQ_KEY_GID 0640`); only the broker and the gateway get it through `group_add` |
| `CQ_SOCKET_GID` | int | set by postinst | no | dgx | Host `crewquarters` group ID: runtime socket access, and read access to `master.key`, `documents` and `embedding-models` |
| `CQ_PROVIDER_MODE` | `fake` \| `live` | dev `fake`, appliance `live` | no | all | Broker provider mode (docs/capability-broker.md) |
| `CQ_EMBEDDING_MODE` | `fake` \| `local` | dev `fake`, appliance `local` | no | all | Knowledge embeddings; `local` makes the `knowledge-model` init service run `cq-knowledge fetch-model` |
| `CQ_EMBEDDING_MODEL_DIR` | path | `/var/lib/crewquarters/embedding-models` (Compose) | no | all | Pinned embedding model directory: a named volume (dev) or the host directory (appliance, `2770 root:crewquarters`). `CQ_EMBEDDING_CACHE_DIR` is set to the same path |
| `CQ_TUNNEL_TOKEN` | secret string | empty | **yes** | demo only | Cloudflare tunnel token for the `callbacks` profile (`crewquarters tunnel up`); keep it in `secrets.env` |
| `CQ_RUNTIME_*` | | | token: yes | host | Runtime daemon settings; see docs/runtime-daemon.md |
