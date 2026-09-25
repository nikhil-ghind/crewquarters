# Compose stacks

| File | Use |
| --- | --- |
| `compose.yaml` | Laptop stack (`dev` profile): `make dev-up` |
| `compose.runtime.yaml` | Override that adds the runtime daemon in a container (dev only): `make integration-up`, `make demo-up` |
| `compose.demo.yaml` | Override on `compose.yaml` + `compose.runtime.yaml`: the control API and scheduler load `.demo/catalog` (the three demo agents pinned in the local registry): `make demo-up` ([local-demo.md](../../docs/runbooks/local-demo.md)) |
| `compose.realstack.yaml` | Override for the real-stack E2E suite, project `cqreal` with ports and images from `realstack.env`: `make realstack-up` ([testing-realstack.md](../../docs/testing-realstack.md)) |
| `compose.appliance.yaml` | Appliance stack, installed by the `.deb` and started by `crewquarters.service` |
| `compose.lan-https.yaml` | Override on `compose.appliance.yaml` for LAN HTTPS mode, added by `crewquarters lan-https enable` ([lan-https.md](../../docs/runbooks/lan-https.md)) |

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
| `tunnel` (profile `callbacks`) | | Named Cloudflare tunnel (`CQ_TUNNEL_TOKEN`) to the proxy's callbacks-only site (port 8081) |
| `tunnel-quick` (profile `callbacks-quick`) | | The same without a Cloudflare account: a random `https://<name>.trycloudflare.com` URL, printed in `docker compose logs tunnel-quick`, that changes on every start (tests only). Set `CQ_TWILIO_CALLBACK_BASE_URL` to it and recreate the broker |

Only `capability-broker` and `model-gateway` mount the keyring, and they read it through `group_add`. To run a second stack beside a running one, use another project name, other ports and other images:

```bash
CQ_HTTP_PORT=18080 CQ_POSTGRES_PORT=15432 CQ_PUBLIC_BASE_URL=http://localhost:18080 \
CQ_PLATFORM_IMAGE=crewquarters/platform:mine CQ_PROXY_IMAGE=crewquarters/proxy:mine \
  docker compose -p mine -f infra/compose/compose.yaml up -d --build --wait
docker compose -p mine -f infra/compose/compose.yaml down -v
```

The tunnels reach only the proxy's callbacks site (Google OAuth and Twilio callback paths, 404 for everything else) over the `callbacks` network ([proxy.md](../../docs/runbooks/proxy.md)).

With `compose.runtime.yaml`, the runtime daemon creates the internal networks `cq-agents` and `cq-models`:

- the broker joins `cq-agents` as `capability-broker`, which agents reach as `PLATFORM_BROKER_URL`;
- the gateway joins `cq-models`.

## Local demo (`make demo-up`)

`compose.yaml` + `compose.runtime.yaml` + `compose.demo.yaml`, in the same Compose project (`crewquarters`) and on the same ports as `make dev-up`:

1. Builds `crewquarters/platform:dev` and `crewquarters/proxy:dev`, and starts `registry` (profile `fake`, `127.0.0.1:5001`).
2. `tests/realstack/prepare.py --registry localhost:5001 --out .demo --no-test-variants` builds the contract probe, Gmail digest and caller images for this machine, pushes them, and writes `.demo/manifests/` and `.demo/catalog/` (the bundled catalog plus the three pinned agents).
3. Starts `runtime-daemon` first (it creates `cq-agents` and `cq-models`), then the rest.

The model gateway runs mock model-server containers through the daemon. Google and Twilio are the broker's fakes unless `LIVE_ENV=<file>` passes an env file with `CQ_PROVIDER_MODE=live` (`--env-file`). `make demo-down` removes agent and model containers and stops the stack; `make demo-down V=1` also deletes the volumes and the run and model data under `CQ_DATA_DIR`. Details, live providers and troubleshooting: [docs/runbooks/local-demo.md](../../docs/runbooks/local-demo.md).

## Real-stack E2E (`compose.realstack.yaml`)

Project `cqreal`, with its own ports (proxy `18083`, PostgreSQL `15435`, registry `15001`, broker admin `18084`), image tags (`:cqreal`) and agent networks (`cqreal-agents`, `cqreal-models`) from `realstack.env`, so it runs beside a developer's stack. The gateway uses its in-process mock, and the broker runs through a test harness that delivers fake Twilio callbacks through the proxy and exposes fault knobs. `make realstack-up`, `make realstack-test`, `make realstack-down`; see [docs/testing-realstack.md](../../docs/testing-realstack.md).

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
