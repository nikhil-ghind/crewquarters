# Compose stacks

| File | Use |
| --- | --- |
| `compose.yaml` | Laptop stack (`dev` profile): `make dev-up` |
| `compose.runtime.yaml` | Override that adds the runtime daemon in a container (dev only): `make integration-up` |
| `compose.appliance.yaml` | Appliance stack, installed by the `.deb` and started by `crewquarters.service` |

## Laptop stack (`compose.yaml`)

| Service | Published | Purpose |
| --- | --- | --- |
| `proxy` | `127.0.0.1:${CQ_HTTP_PORT:-8080}` | The only HTTP entry: web UI, `/api/*` to the control API, OAuth/Twilio callbacks to the broker, and 404 for `/internal/*` ([docs/runbooks/proxy.md](../../docs/runbooks/proxy.md)) |
| `postgres` | `127.0.0.1:${CQ_POSTGRES_PORT:-55432}` | PostgreSQL 16 + pgvector (also used by the test suite) |
| `migrate` | | One-shot `alembic upgrade head` |
| `master-key` | | One-shot: a random development keyring in the `master-key` volume, `root:${CQ_KEY_GID:-10500}` 0640 |
| `control-api`, `scheduler` | | Control plane |
| `capability-broker` | | `cq-broker`; fake Google/Twilio by default (`CQ_PROVIDER_MODE`); reads the keyring |
| `knowledge-model` | | One-shot `cq-knowledge fetch-model` when `CQ_EMBEDDING_MODE=local` (the default `fake` needs no model) |
| `knowledge` | | `cq-knowledge`; `documents` and `embedding-models` volumes |
| `model-gateway` | | In-process mock models; reads the keyring for cloud keys |
| `tunnel` (profile `callbacks`) | | Cloudflare tunnel to the proxy's callbacks-only site (port 8081) |

Only `capability-broker` and `model-gateway` mount the keyring, and they read it through `group_add`. To run a second stack beside a running one, use another project name, other ports and other images:

```bash
CQ_HTTP_PORT=18080 CQ_POSTGRES_PORT=15432 CQ_PUBLIC_BASE_URL=http://localhost:18080 \
CQ_PLATFORM_IMAGE=crewquarters/platform:mine CQ_PROXY_IMAGE=crewquarters/proxy:mine \
  docker compose -p mine -f infra/compose/compose.yaml up -d --build --wait
docker compose -p mine -f infra/compose/compose.yaml down -v
```

With `compose.runtime.yaml`, the runtime daemon creates the internal networks `cq-agents` and `cq-models`:

- the broker joins `cq-agents` as `capability-broker`, which agents reach as `PLATFORM_BROKER_URL`;
- the gateway joins `cq-models`.

## SDK/agent development stack (profile `fake`)

| Service | Port | Purpose |
| --- | --- | --- |
| `fake-platform` | `127.0.0.1:8090` | Fake control-API slice, broker, model gateway, knowledge, Gmail/Sheets/Twilio (`/fake/v1` admin API for tests) |
| `registry` | `127.0.0.1:5001` | Local OCI registry, so agents run pinned by digest, as the real daemon runs them |

`crewq-agents` is `internal: true`, so it has no internet egress. Agent containers attach to it and reach only the alias `broker`, which is the fake platform. The fake platform mounts the repository read-only at `/workspace` and resolves scenario paths relative to it (for example `tests/fixtures/scenarios/demo`).

For a realistic local model, point the fake's `local.general.*` profiles at any OpenAI-compatible server before `make fake-up`:

```bash
export CREWQ_FAKE_LLM_BASE_URL=http://host.docker.internal:11434/v1   # e.g. Ollama
export CREWQ_FAKE_LLM_MODEL=llama3.2:3b
```
