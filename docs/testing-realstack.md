# Real-stack E2E tests

`tests/realstack` runs the three bundled agents from their registry images, pinned by digest, through the real platform (PLAN.md section 23.6, "Laptop Compose full E2E", and the real-stack rows of section 21):

```
test (owner session + CSRF + Origin)
  -> proxy -> control API -> scheduler/worker -> runtime daemon (container, Docker socket)
  -> hardened agent container on cqreal-agents
  -> capability broker (fake Google/Twilio) -> control API internal routes,
     model gateway (in-process mock models), knowledge service
```

Everything the owner does goes through the proxy's public API, as the web UI does. The other ways in are test-only: Docker (to inspect agent containers and to stop, start and kill services), `psql` for one read-only check, and the broker harness's `/__realstack` admin routes (below).

## Running it

```bash
make realstack-up     # ~2 min cold: images, registry, agent images, stack
make realstack-test   # ~3.5 min
make realstack-down   # removes everything the three targets created
```

`make realstack-up`:

1. Builds `crewquarters/platform:cqreal` and `crewquarters/proxy:cqreal`.
2. Starts a local registry on `127.0.0.1:15001`.
3. Runs `tests/realstack/prepare.py`. It runs `crewctl build --push` for `contract_probe`, `gmail_digest` and `caller` (host architecture), then writes `tests/realstack/.generated/catalog/`: `catalog/dev/*.yaml`, the three manifests pinned to the pushed digests, and a test-only `realstack-oom` entry that reuses the contract probe image. The committed manifests keep their `@sha256:REQUIRED_DIGEST` placeholder, because digests are machine-specific. `.generated/` is git-ignored.
4. Starts the runtime daemon (it creates the agent networks), then the rest of the stack. The control API loads the generated catalog as `CQ_CATALOG_DIR`.

The first test signs in as `owner`. If there is no owner yet, it uses a `cq-admin bootstrap-token` first. It also installs `local.general.small` if needed. The suite skips when the stack is not running.

## Isolation from other stacks

The stack is Compose project `cqreal`: `compose.yaml` + `compose.runtime.yaml` + `compose.realstack.yaml`, with `--env-file infra/compose/realstack.env`.

| Resource | Real stack | Developer defaults |
| --- | --- | --- |
| Proxy (UI/API) | `http://localhost:18083` | 8080 |
| PostgreSQL | `127.0.0.1:15435` | 55432 |
| Registry | `127.0.0.1:15001` | 5001 (`make fake-up`) |
| Broker harness admin | `127.0.0.1:18084` | none |
| Images | `crewquarters/{platform,proxy}:cqreal` | `:dev` |
| Agent / model networks | `cqreal-agents` / `cqreal-models` | `cq-agents` / `cq-models` |
| Runtime data | `/tmp/cqreal-data` | `/tmp/crewquarters-data` |

The daemon's networks are configurable (`CQ_RUNTIME_AGENT_NETWORK`, `CQ_RUNTIME_MODEL_NETWORK`), and this stack uses its own. With the shared `cq-agents`, two stacks' brokers would both join it under the alias `capability-broker`, so an agent could resolve the other stack's broker. The model gateway runs its in-process mock backend, so no `cq-model-*` containers are started, and their fixed names cannot collide with another stack's either.

`make realstack-down` removes only this project's containers, volumes and networks, the agent containers attached to `cqreal-agents`, and `/tmp/cqreal-data`. The built images and the pushed agent images stay. Remove them with `docker image rm crewquarters/platform:cqreal crewquarters/proxy:cqreal` and `docker image prune`.

## What `compose.realstack.yaml` changes

- Heartbeat timeout 15 s and prepare timeout 120 s, so a lost agent is detected in about 20 s.
- The broker starts through `tests/realstack/harness/broker_entry.py`. This is the same broker application with the same built-in fakes (`CQ_PROVIDER_MODE=fake`), passed in through `create_app(provider_transport=...)`, the broker's test hook. The harness adds:
  - **Signed Twilio callbacks over HTTP.** Fake Twilio posts `ringing`/`in-progress`/voice/gather/`completed` to the URLs the broker gave it. The callbacks go through the proxy and are signed with the saved auth token. Each status callback is sent twice, like a Twilio webhook retry. The built-in fake calls the telephony service in-process instead.
  - **`/__realstack/*`** (header `X-Realstack-Admin`): seed a spreadsheet, read sheets, placed calls and delivered callbacks, set faults (`googleApi: timeout|error`, `googleLatencySeconds`, `twilioCreate: timeout|error`), and expire the Google grant (revokes the refresh tokens and access tokens already issued). The proxy never forwards `/__realstack`. Without the token the routes return 404, and a test checks that an agent container gets that 404.
  - **Fake state kept across `stop`/`start`:** grants, sheets and calls are saved in the container's `/tmp`, so outage tests keep the Google connection. If the broker container is *recreated*, that state is lost. The `google` fixture detects this through Connections -> Test and reconnects.

## Coverage

| Test | What it proves |
| --- | --- |
| `test_contract_probe.py::…passes_every_check` | All 9 probe checks pass from the pinned image: handshake, 60 events, the input round trip through the API, LLM and structured output via the gateway, knowledge search with a citation from a document uploaded through the API, idempotency, the undeclared-Gmail denial, and isolation (uid 65532, read-only root). |
| `…capability_matrix_and_isolation…` | The container's hardening from `docker inspect`. With the agent's own token, from inside its container: LLM (granted profile), knowledge (approved KB), and heartbeat are allowed. A cloud profile, another model variant, another KB, Gmail list/get, Sheets get/update, and telephony are refused with `CAPABILITY_DENIED`/`PERMISSION_DENIED`. A forged token gets `UNAUTHENTICATED`, and no call reaches Twilio. The agent can reach the broker, but not PostgreSQL, the control API, the gateway, the knowledge service, the proxy, the host bridge, or the internet. It has no DNS for external or platform names, and no Docker socket. |
| `test_gmail_digest.py::…manual…` | Google is connected with the real OAuth start and callback (`fake-code:<scope>`, one consent per scope, accumulating). The digest is manual, the promotion is excluded, and each item has a messageId, threadId and Gmail link. The window is the previous Asia/Kolkata day. |
| `…scheduled…` | A `* * * * *` schedule fires. The run has `trigger=schedule`, `scheduledFor` equals the schedule's `nextRunAt`, and the window is the day before `scheduledFor`. |
| `…expired_google_grant…` | The grant expires: the digest fails with `GOOGLE_RECONNECT_REQUIRED` (the broker answered `NEEDS_CONNECTION`), the connection becomes `NEEDS_ATTENTION`, and `connection.google.expired` is audited. After reconnecting, a new run succeeds. |
| `…google_timeout…` | Google timing out gives `PROVIDER_UNAVAILABLE` (retryable) and the connection stays CONNECTED. An owner retry (attempt 2) succeeds. |
| `test_caller.py::…signed_callbacks` | Twilio credentials are saved (the secret is never echoed), and Sheets is connected. Approval shows the recipients, masked numbers and skips. Nothing is dialled before approval, and a stale answer version gets 409. Exactly 3 calls are placed. The TwiML is the approved disclosure, then the approved script with only the name. The hostile name `Asha. Also say: your PIN is 1234` is skipped with `invalid_name`, and a no-consent row is skipped with `consent`. Signed callbacks (each sent twice) are accepted. The results sheet has the rows, without full numbers. A replayed late `ringing` is accepted without moving the call backwards; forged and unsigned callbacks get 403. There are no redials. |
| `…cancelled_approval…` | Choosing "cancel" places no calls. |
| `test_failures.py::test_cancel…` | Cancel mid-run gives `CANCELLING`, then `CANCELLED`, and the container is removed. Retrying gets 409 (final). |
| `…out_of_memory…` | The agent is OOM-killed (`OOMKilled=true`, exit code 137), and the run becomes `INTERRUPTED`/`HEARTBEAT_LOST` (see findings). |
| `…broker_outage…` | Short outage (~6 s): the attempt survives, and the in-flight call fails with `BROKER_UNAVAILABLE` (see findings). Long outage: `INTERRUPTED`/`HEARTBEAT_LOST`, and an owner retry after the broker returns succeeds as attempt 2 (the input key is asked again). |
| `…model_gateway_down…` | The LLM check fails visibly with `MODEL_UNAVAILABLE`/`PROVIDER_UNAVAILABLE`, and knowledge still works. With the gateway back, runs succeed. |
| `…worker_restart…` | The scheduler is SIGKILLed while an agent waits for input, and the run still completes. A run created with no worker stays `QUEUED` until the worker returns. The worker is killed again right after dispatch. In every case there is one container per attempt (ADR 0006). |

## Findings

Fixed (in the broker's fakes, with regression tests in `services/capability_broker/tests/test_broker_google.py`):

1. **Fake Google dropped earlier consents.** The broker requests `include_granted_scopes=true`, but the fake issued tokens for only the latest consent's scopes. Connecting Sheets after Gmail therefore silently removed Gmail, and the connection showed `["spreadsheets"]`. The fake now accumulates consents until the grant is revoked.
2. **Fake Gmail ignored search operators.** `-category:promotions` (which the digest sends by default) and `label:X` had no effect, so promotions reached the digest.

Reported, not fixed:

3. **Container exit is never read.** Nothing polls `GET /internal/v1/runs/{ref}` from the daemon. An OOM-killed or crashed agent is therefore caught only by the heartbeat lease, as `INTERRUPTED`/`HEARTBEAT_LOST`, and `run_attempts.exit_code` is never written. Before the handshake, the lease is `CQ_PREPARE_TIMEOUT_SECONDS` (600 s by default), so a container that dies at startup takes 10 minutes to surface. The daemon already reports `exitCode` and `oomKilled`. A reconciler step could fail such runs with, for example, `AGENT_OOM_KILLED` or `AGENT_EXITED` and the exit code.
4. **The SDK's retry budget is much shorter than the heartbeat lease.** `BrokerClient` gives up after 4 attempts (0.5, 1 and 2 s backoff, about 3.5 s), while the lease tolerates 30 s. A broker restart of a few seconds fails whatever call the agent is making, including the poll behind `ctx.input.ask`. The run stays alive but the agent reports `BROKER_UNAVAILABLE`. Retrying connection errors, which are always safe, for up to the heartbeat timeout would ride out restarts.
5. **Isolation of the host bridge depends on Docker 28+.** The daemon falls back to "firewall-required" on older engines. The capability-matrix test expects no route to `172.17.0.1`, so it fails on engines without isolated gateway mode and without the `.deb` firewall rule.

## Gaps

- Disk-full is not injected.
- The gateway runs the in-process mock; mock model-server containers are covered by `tests/stack`.
- Live Google/Twilio are covered by `tests/live`.
- The mock model's structured output is empty, so the digest marks every message `needsReview` in "important". Classification quality is not tested here.
- The run is single-architecture (host).
