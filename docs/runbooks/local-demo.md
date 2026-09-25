# Local demo on a laptop (`make demo-up`)

`make demo-up` runs the whole platform on one machine: the proxy and web UI, control API,
scheduler, capability broker, knowledge service, model gateway, PostgreSQL, and the runtime
daemon in a container. Agents run in real hardened containers, and models run as mock
model-server containers. The Gmail digest, caller and contract probe agents are built for this
machine, pushed to a local registry, and pinned by digest in the catalog the platform loads.

Google and Twilio are the broker's built-in fakes unless you pass a live env file (see
[Going live](#going-live-with-google-and-twilio)).

The runtime daemon in a container mounts the Docker socket. That is for development only; the
appliance runs the daemon as a host service ([dgx.md](dgx.md)).

## Prerequisites

- Docker with Compose v2 and buildx (Docker's default builder pushes to `localhost:5001`; a
  `docker-container` builder needs `driver-opts: network=host`).
- [uv](https://docs.astral.sh/uv/). `prepare.py` and `crewctl` run from the Python workspace.
- Node 22.18 or later, only for UI development in `apps/web` ([web-ui.md](../web-ui.md)). The
  demo's UI is built inside the proxy image.
- Internet access on the first run: base images, Python packages, and the pinned `python` image
  that the mock model server runs in.

## Ports

| Port | Service | If it is taken |
| --- | --- | --- |
| `127.0.0.1:8080` | Proxy: UI and `/api` | `CQ_HTTP_PORT=18080`, and set `CQ_PUBLIC_BASE_URL=http://localhost:18080` so OAuth redirects and the Origin check use the new port |
| `127.0.0.1:55432` | PostgreSQL | `CQ_POSTGRES_PORT=15432`. The test suite expects 55432 unless you set `CQ_TEST_ADMIN_URL` |
| `127.0.0.1:5001` | Local registry (`registry`, profile `fake`) | Fixed in `infra/compose/compose.yaml` and in the Makefile's `REGISTRY`. Free the port, or edit both |

Compose reads these variables from your shell, so export them before every `make demo-*` and
`docker compose` command, for example `export CQ_HTTP_PORT=18080
CQ_PUBLIC_BASE_URL=http://localhost:18080`.

Nothing else is published. The broker, gateway, knowledge service, control API and runtime
daemon are reachable only on Compose networks.

## First run

```bash
make sync            # the Python workspace
make demo-up         # images, registry, agent images, catalog (.demo/), runtime daemon, stack
make dev-bootstrap   # prints a one-time owner setup code
```

`make demo-up` does, in order:

1. Builds `crewquarters/platform:dev` and `crewquarters/proxy:dev`.
2. Starts the local registry on `127.0.0.1:5001`.
3. Runs `tests/realstack/prepare.py --registry localhost:5001 --out .demo --no-test-variants`.
   This builds the three agent images for this machine's architecture, pushes them, and writes
   pinned manifests to `.demo/manifests/` and the catalog to `.demo/catalog/` (the bundled
   `hello-crew` plus the three agents).
4. Starts the runtime daemon first, because it creates the `cq-agents` and `cq-models` networks
   the broker and gateway join, and then the rest of the stack from `compose.yaml`,
   `compose.runtime.yaml` and `compose.demo.yaml`.

Open **http://localhost:8080/setup**. Use `localhost` rather than `127.0.0.1`: `CQ_PUBLIC_BASE_URL`
is `http://localhost:8080`, so the Google OAuth redirect returns you to `localhost`, and the
session cookie belongs to the host you signed in on.

The setup wizard:

1. **Welcome**, **System preflight**.
2. **Owner account**: paste the setup code from `make dev-bootstrap`. It works once and expires
   after 24 hours; run the command again for a new one.
3. **Storage and network**, **Platform services**: checks only.
4. **Local model**: install **General (small) - mock** (`local.general.small`). Installing
   copies the mock server file. The first load pulls the pinned `python` image.
5. **Connections**: connect Google (see below). Twilio, OpenAI and Anthropic are under
   "Optional".
6. **Demo agents**: install Daily Gmail Digest and Caller. You approve every permission.
7. **Validation and finish**.

### Connecting the fake Google account

With fake providers, **Connect Google** still sends the browser to `accounts.google.com` with a
placeholder client ID (`crewquarters-dev.apps.example.com`), and Google shows an error. Finish the
sign-in by hand:

1. Copy the `state` value from the address bar of Google's error page.
2. In the same browser, open
   `http://localhost:8080/api/v1/connections/google/callback?state=<state>&code=fake-code`.

`code=fake-code` grants both scopes (`gmail.readonly` and `spreadsheets`), and
`code=fake-code:gmail.readonly` grants only Gmail. The state is single-use, expires after
10 minutes, and must be used in the browser that started the sign-in. The broker returns you to
Connections with the result.

The fake keeps its grants in the broker's memory. After the broker container is restarted or
recreated (including `make demo-down` and `make demo-up`), Google shows **Needs attention**:
connect it again the same way.

## Trying the agents with fake providers

Install and run agents from **Crew**. Pending questions ("Crew Requests") appear on Overview,
under Activity, and on the run's page.

**Daily Gmail Digest.** Set `timezone` to your own IANA zone. The fake Gmail inbox has eight
messages, created when the broker starts and dated 24 hours earlier: a plain-text standup note,
a multipart invoice reminder, an HTML-only security alert, an empty message, an attachment-only
report, a prompt-injection message ("IGNORE PREVIOUS INSTRUCTIONS…"), a promotion, and one with
malformed base64. The promotion is excluded by the default `excludeCategories`. Because the
fixtures are 24 hours older than the broker's start, a broker that has been running since an
earlier day returns an empty day; restart it (`docker compose -f infra/compose/compose.yaml
restart capability-broker`) and reconnect Google.

With the mock model, every message lands under **Important** with **Needs review**: the mock
returns an empty classification, and the agent never hides unclassified mail. See
[Limitations](#limitations-on-a-laptop).

**Caller.** Save a fake Twilio account under Connections: an account SID of `AC` followed by 32
hex digits, an auth token of 16 to 128 characters, and an E.164 caller number, for example
`+15555550100`. **Place a test call** works and places a fake call. The fake Sheets start empty
and are kept in the broker's memory, and nothing in the UI writes contacts into them. A caller
run under `make demo-up` therefore finds no eligible rows and succeeds without asking for
approval or calling anyone. To see the approval and the calls:

- use live Google and Twilio ([Going live](#going-live-with-google-and-twilio)); or
- run `make realstack-up && make realstack-test`. The real-stack suite seeds the fake sheet
  through a test-only broker harness and checks the approval, the three calls and the signed
  callbacks ([testing-realstack.md](../testing-realstack.md)); or
- use the fake platform: `make fake-up`, `make e2e-images`, `make demo-seed`,
  `make demo-run AGENT=caller`, then `make demo-approve` ([operator-script.md](../demo/operator-script.md)).

When the fake does place calls, the destination's last digit picks the outcome: 2 busy,
3 no-answer, 4 failed, 5 answered without speech, anything else answered with the transcript
"Yes, I can attend." ([capability-broker.md](../capability-broker.md)).

**Contract probe.** It needs a knowledge base: create one first (next section) and choose it as
`knowledgeBaseId`. Its `input` check asks a Crew Request; answer it. All other checks, including
`isolation` in the hardened container, run without input.

**Hello Crew** (`hello-crew`) is in the bundled catalog for the fake runtime used by
`make dev-up`. Its image reference is a placeholder, so it cannot run under `make demo-up`.

## Knowledge and chat

1. **Knowledge** → create a knowledge base → **Upload documents**: UTF-8 text, Markdown, CSV,
   DOCX, or PDF with embedded text, up to 25 MiB each. Each document shows its state; a scanned
   PDF fails with a clear error.
2. **Test retrieval** on the knowledge base page.
3. **Chat** → **Enable chat** with the local model and the knowledge base. Enabling takes a model
   lease (a cold start); disabling releases it, and the model unloads after its idle grace.

The mock model answers a grounded question with a quote from the first retrieved passage and
its citation (`Mock answer from the knowledge base: "…" [citation]`), and echoes other
messages. The dev stack's embeddings are `fake.hashing-512`: they match words, not meaning. For
the pinned CPU embedding model (about 130 MB, downloaded once into the `embedding-models`
volume):

```bash
CQ_EMBEDDING_MODE=local make demo-up
```

A knowledge base keeps the embedding profile it was created with. After switching modes,
existing knowledge bases report `EMBEDDING_PROFILE_MISMATCH` until they are re-indexed
([knowledge.md](../knowledge.md)).

## Going live with Google and Twilio

Put the live settings in a file outside the repository, readable only by you
(`chmod 600 ~/crewquarters-live.env`):

```bash
CQ_PROVIDER_MODE=live
CQ_GOOGLE_CLIENT_ID=<client id>.apps.googleusercontent.com
CQ_GOOGLE_CLIENT_SECRET=<client secret>
# Optional, for the caller:
CQ_TWILIO_ALLOWED_NUMBERS=["+15551234567","+15557654321"]
CQ_TWILIO_CALLBACK_BASE_URL=https://<name>.trycloudflare.com
```

```bash
make demo-up LIVE_ENV=~/crewquarters-live.env
```

`CQ_PROVIDER_MODE=live` switches both Google and Twilio; you cannot keep one of them fake. Pass
the same `LIVE_ENV` to every later `make demo-up`. A `docker compose up` without the file
recreates the broker in fake mode.

### Google OAuth client

In the Google Cloud console:

1. Create a project and enable the **Gmail API** and the **Google Sheets API**.
2. Configure the OAuth consent screen: user type **External**, publishing status **Testing**.
   Add the scopes `https://www.googleapis.com/auth/gmail.readonly` and
   `https://www.googleapis.com/auth/spreadsheets`, and add the Google account you will connect
   as a **test user**.
3. Create an OAuth client ID of type **Web application** with the authorized redirect URI
   `http://localhost:8080/api/v1/connections/google/callback`. It must match
   `CQ_PUBLIC_BASE_URL` exactly; register another URI if you changed the port.
4. Put the client ID and secret in the live env file.

Connect from **http://localhost:8080** → Connections. Google warns that the app is not verified;
continue as the test user. In Testing mode the grant and refresh token expire after **seven
days**. The connection then shows **Needs attention**, and the digest fails with
`GOOGLE_RECONNECT_REQUIRED` until you reconnect.

For the caller, make a Google Sheet with a `Contacts` tab (columns `name`, `phone_e164`,
`consent`, `status`; header in row 1, data from row 2) and an empty `Results` tab, and give the
caller its spreadsheet ID. Consent must be `yes`, `true` or `consented`. Results are written to
the same row numbers as the contacts ([agents/caller/README.md](../../agents/caller/README.md)).

### Twilio

- A **trial account** can call only **verified caller IDs**. Verify each recipient in the Twilio
  console, and list the same numbers in `CQ_TWILIO_ALLOWED_NUMBERS`. In live mode the broker
  refuses any other destination, including test calls. Trial calls start with Twilio's trial
  message before the script plays.
- Save the account SID, auth token and your Twilio number under Connections → Twilio. The
  secret is never shown again. **Place a test call** needs no callbacks.
- A caller run does need callbacks: Twilio fetches the call's TwiML from, and posts status and
  speech to, `CQ_TWILIO_CALLBACK_BASE_URL`. Without a public URL, calls cannot proceed.

A quick Cloudflare tunnel needs no account. It reaches only the proxy's callbacks site (port
8081), which serves the Google and Twilio callback paths and returns 404 for everything else:

```bash
DEMO="docker compose -f infra/compose/compose.yaml --env-file $HOME/crewquarters-live.env \
  -f infra/compose/compose.runtime.yaml -f infra/compose/compose.demo.yaml"
$DEMO --profile callbacks-quick up -d tunnel-quick
$DEMO --profile callbacks-quick logs tunnel-quick | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com'
```

Set `CQ_TWILIO_CALLBACK_BASE_URL` in the live env file to the printed URL, then apply it.
Compose recreates the services whose environment changed, the broker among them
(`make demo-up LIVE_ENV=...` does the same, after rebuilding the images):

```bash
$DEMO up -d --wait
```

The URL changes every time the tunnel starts, so repeat this after each restart of
`tunnel-quick`. Keep `CQ_PUBLIC_BASE_URL` at `http://localhost:8080`: Google sign-in stays on
localhost. A named tunnel with a token uses the `callbacks` profile instead
(`CQ_TUNNEL_TOKEN`; [proxy.md](proxy.md)). Stop the quick tunnel when you are done, before
`make demo-down`:

```bash
docker compose -f infra/compose/compose.yaml --profile callbacks-quick rm -sf tunnel-quick
```

Use only numbers whose owners have agreed to test calls.

## Limitations on a laptop

- **No real classification.** The dev model catalog serves a deterministic mock, so every digest
  item is "needs review". The bundled agents request only the `local.general` family and no
  cloud provider (`cloudProviders: []`), so an OpenAI or Anthropic key does not change this;
  chat is local-only too. Real classification needs the GB10 catalog with vLLM ([dgx.md](dgx.md)),
  or the fake platform pointed at an OpenAI-compatible server (`CREWQ_FAKE_LLM_BASE_URL`,
  [infra/compose/README.md](../../infra/compose/README.md)).
- Cloud keys can still be saved and tested under Connections; the gateway uses them only for a
  profile an agent requests and the owner approved.
- `CQ_DATA_DIR` (default `/tmp/crewquarters-data`) holds model files and per-run data. If your
  system clears `/tmp` at boot, set `CQ_DATA_DIR` to a persistent directory for every demo
  command. It must be the same path inside and outside the containers.

## Stopping and resetting

| Command | Effect |
| --- | --- |
| `make demo-down` | Removes this stack's agent and model containers (those the runtime daemon attached to `cq-agents`/`cq-models`), stops the stack and removes the registry. Volumes and run data are kept |
| `make demo-down V=1` | The same, and also deletes the `runs` and `models` directories under `CQ_DATA_DIR` and every volume of the `crewquarters` Compose project: the database, master key, documents, embedding model and backups |
| `docker compose -f infra/compose/compose.yaml exec control-api cq-admin demo reset --yes` | Between rehearsals: cancels active runs, releases chat leases, and deletes runs, calls, action claims and chat sessions. Users, installations, schedules, connections, models and knowledge bases stay; `--schedules` and `--knowledge` remove those too ([backup-restore.md](backup-restore.md)) |

`make demo-down V=1` deletes the same PostgreSQL volume that `make db-up`, `make dev-up` and the
test suite use. Run `make db-up` before `make test-platform` again.

## Troubleshooting

- **Logs.** `make dev-logs` follows the control API and scheduler. For another service:
  `docker compose -f infra/compose/compose.yaml -f infra/compose/compose.runtime.yaml -f
  infra/compose/compose.demo.yaml logs -f capability-broker` (or `model-gateway`, `knowledge`,
  `runtime-daemon`, `proxy`). Agent output is on the run's page; the containers are
  `docker ps -a --filter label=io.crewquarters.kind=run`.
- **A stuck run.** Cancel it from its page. A run waiting for a Crew Request is not stuck: it
  waits up to its manifest's `maxInputWaitSeconds`. An attempt's lease is
  `CQ_PREPARE_TIMEOUT_SECONDS` (600 s) before its handshake, for example during a slow image
  pull, and `CQ_HEARTBEAT_TIMEOUT_SECONDS` (30 s) after it; when the lease expires the run becomes
  `INTERRUPTED` (`HEARTBEAT_LOST`) and can be retried. A container that exits is reported within
  about 2 s (`AGENT_EXITED`, `AGENT_OUT_OF_MEMORY`). If runs stay in
  `CANCELLING`, check that the scheduler is running, then use
  `cq-admin demo reset --yes --force`.
- **Port conflicts.** See [Ports](#ports). `Bind for 127.0.0.1:5001 failed` usually means another
  registry is running; `make fake-up` starts the same `registry` service, which `demo-up` reuses.
- **The existing dev database.** `make demo-up` uses the Compose project `crewquarters`, the same
  as `make db-up` and `make dev-up`, so it reuses the running `crewquarters-postgres-1` container
  and its data. The test suite also creates throwaway databases on that server
  (`CQ_TEST_ADMIN_URL`, default `127.0.0.1:55432`), and some tests run `docker exec` in that
  container.
- **Switching between `dev-up` and `demo-up`.** Both use the same project. Run `make demo-down`
  before `make dev-up`, or Compose leaves the runtime daemon running as an orphan and the control
  API goes back to the fake runtime.
- **Agent images out of date.** `make demo-up` rebuilds and re-pins them each time. After
  editing an agent, run it again.
