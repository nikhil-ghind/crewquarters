# Release checklist (PLAN.md §23)

Every §23 item is listed here with its Must/Should tag, owner, evidence, and status. A failed
**Must** blocks the demonstration (§26). A failed **Should** goes into the known-limitations list
below.

Status values: **Done (fake)** has automated evidence against the fake platform in CI. **Partial**
means Person 5's side is done and another owner's side is pending. **Pending** means it is not
started or belongs to another owner.

Evidence commands: `make test` (control plane, SDK, agents, contract, and integration tests; needs
PostgreSQL), `make test-sdk` (no database), `make fake-up && make e2e` (Docker), `make agent-images`
(amd64 and arm64), `make evidence` (writes a report to `evidence/<UTC>/`).

## 23.1 Repository and contracts

| Tag | Item | Owner | Evidence | Status |
| --- | --- | --- | --- | --- |
| Must | Monorepo layout matches or updates the boundaries through an ADR | Person 1 (all) | `packages/`, `agents/`, `infra/compose`, `tests/` follow the README layout | Partial: Person 5 areas done |
| Should | README and operator/developer/security runbooks are current | All | `docs/sdk/*`, `docs/demo/operator-script.md`, README developer quickstart | Partial |
| Must | OpenAPI, event schemas, manifest schema, generated clients, and examples are versioned | Person 1, Person 3 | Canonical `packages/contracts/*` from Person 1 (`tests/contract/test_contracts.py`). Person 5 checks against them: the bundled manifests pass the control plane's validation, the fake's traffic matches `openapi.yaml` and the run-event schema, and the broker draft mirrors `/internal/v1` (`tests/contract/test_broker_contract_files.py`, `test_fake_route_parity.py`, `test_fake_traffic_conformance.py`) | Partial: `broker-sdk.openapi.yaml` awaits Person 3 ([D1](../decisions/0001-person5-contract-drafts.md)) |
| Should | ADRs cover all fixed decisions in §1 | Person 1 | `docs/decisions/0001-person5-contract-drafts.md` | Partial |
| Must | License inventory and notices exist for bundled models and code | Person 1 (release) | — | Pending |

## 23.2 Core product

| Tag | Item | Owner | Evidence | Status |
| --- | --- | --- | --- | --- |
| Must | Owner bootstrap, login, logout, session expiry, CSRF | Person 1 | — | Pending |
| Must | Marketplace list, install, config, permission reapproval on update, uninstall | Person 1, Person 4 | The control plane's own tests (Person 1); the fake mirrors exact-approval installs (`test_control.py`) | Partial (install and list done by Person 1; UI pending) |
| Must | Manual and scheduled runs, cancel/retry, events, results, audit | Person 1 | SDK and fake lifecycle (`test_agent_lifecycle.py`, `test_broker_core.py`, `test_hello_agent.py`) | Partial |
| Must | Exact-10:00 test passes for at least three IANA timezones | Person 1 | The digest's previous-day window is exact for Kolkata, New York, Santiago, and Lord Howe (`test_window.py`) | Partial: scheduler belongs to Person 1 |
| Must | Agent web-input request and answer flow works and handles timeout/restart honestly | Person 1, Person 5 | `test_input.py`, `test_broker_core.py`, a killed agent resumes with the stored answer (`test_hello_agent.py`, `test_caller.py`) | Partial |
| Must | One PostgreSQL database persists and restores all intended state | Person 1 | — | Pending |

## 23.3 Models and chat

| Tag | Item | Owner | Status |
| --- | --- | --- | --- |
| Must | At least one non-gated local model is pinned and validated on GB10/vLLM | Person 2 | Pending |
| Must | Download progress, checksum, disk-space failure, load progress, ready/error states | Person 2 | Pending |
| Must | Concurrent lease requests start only one instance | Person 2 | Pending |
| Must | Admission controller rejects unsafe concurrent loads | Person 2 | Pending |
| Must | Chat is opt-in, RAG citations resolve, disable releases its lease | Person 2, Person 3, Person 4 | Pending |
| Must | Unused model unloads within the expected memory envelope | Person 2 | Pending |
| Must | OpenAI and Anthropic only with explicit profiles/permissions, audited | Person 2 | Partial: the SDK never falls back from local to cloud (`test_llm.py::test_local_family_never_resolves_to_cloud`) |

## 23.4 Knowledge and connections

| Tag | Item | Owner | Evidence | Status |
| --- | --- | --- | --- | --- |
| Must | Supported document formats index; unsupported scans fail clearly | Person 3 | — | Pending |
| Must | Retrieval is scoped to the selected knowledge base | Person 3 | The fake enforces bound KBs (`test_broker_connectors.py::test_knowledge_search_is_scoped_to_the_configured_base`) | Pending (real) |
| Must | Prompt-injection fixtures cannot obtain or invoke capabilities | Person 3, Person 5 | `digest-injection` scenario, `test_gmail_digest.py::test_prompt_injection_…` | Done (fake) for agents |
| Must | Google OAuth state/refresh/reconnect/disconnect; 7-day test expiry documented | Person 3 | The digest and caller map expired Google access to `GOOGLE_RECONNECT_REQUIRED`; operator script notes the 7-day expiry | Partial |
| Must | OAuth/provider secrets encrypted and absent from API reads, logs, and agent environments | Person 3 | The agent environment carries only the run token (`DockerLauncher.command`); SDK redaction (`test_redact.py`) | Partial |
| Must | Twilio webhook signatures verified; duplicates harmless | Person 3 | Duplicate call creation is harmless (`test_caller.py::test_dropped_create_call_…`) | Partial |

## 23.5 Demo agents (Person 5)

| Tag | Item | Evidence | Status |
| --- | --- | --- | --- |
| Must | Gmail digest selects exactly the previous local calendar day and handles pagination and MIME | `test_window.py`, `test_gmail_digest.py` (volume, DST, malformed), `test_mime.py` | Done (fake); live pending |
| Must | Digest groups items with reasons, actions, and traceable message references | `test_gmail_digest.py::test_basic_…`, `test_reduce.py`, result schema check | Done (fake) |
| Must | Caller reads Sheets, enforces consent/E.164/cap, and asks for operator approval | `test_rows.py`, `test_approval.py`, `test_caller.py` | Done (fake) |
| Must | Caller completes a fixed-script speech gather on verified test numbers | `test_caller.py::test_every_call_state_is_visible` (fake Twilio) | Done (fake); live pending Person 3's broker and credentials |
| Must | Results write to the configured tab and retry without duplicate calls | `test_caller.py` (write retries, dropped creates, interrupted retry) | Done (fake) |
| Must | Both agent images run on `linux/amd64` and `linux/arm64` | `make images` (multi-arch manifests), amd64 self-check under emulation, CI `arm64-images` job | Done |

## 23.6 Deployment and security

| Tag | Item | Owner | Evidence | Status |
| --- | --- | --- | --- | --- |
| Must | Laptop Compose profile passes the full fake E2E | Person 5 | `make fake-up && make e2e` (CI `agent-e2e` job) | Done |
| Must | Fresh GB10 installer and uninstall-with-data-preservation rehearsed | Person 2 | — | Pending |
| Must | Runtime daemon is Unix-socket only; internal services not externally published | Person 2 | The laptop stack publishes only on 127.0.0.1 | Pending (real) |
| Must | Agent cannot access Docker, the DB, vLLM directly, host paths/gateway, or the internet | Person 2, Person 5 | contract-probe `isolation` passes in the hardened container on the internal network (`test_e2e_agents.py`) | Partial: the real daemon must apply the same flags |
| Must | Core/agent images are non-root, pinned by digest, scanned, and have SBOMs | Person 5 (agents), Person 2 (core) | Agents are non-root (`test_images_run_as_non_root_…`) and pinned by digest | Partial: scans and SBOMs are Stage 6 work |
| Must | Backup/restore and reboot tests pass | Unassigned ([D16](../decisions/0001-person5-contract-drafts.md)) | — | Pending |
| Must | Fixed callback tunnel exposes only callback routes and can be disabled | Person 3, Person 2 | — | Pending |
| Must | Diagnostics bundle is secret-redacted | Unassigned ([D16](../decisions/0001-person5-contract-drafts.md)) | — | Pending |

## 23.7 UI and operator experience (Person 4)

All items are **Pending** until the UI exists. Person 5 supplies the inputs the UI renders:

- the input-request `preview` object (`blocks`, `choices`, `consequence`);
- result renderer ids (`x-crewquarters-renderer` in each manifest's `resultSchema`:
  `crewquarters.gmail-digest/v1`, `crewquarters.caller/v1`, `crewquarters.contract-probe/v1`);
- the config UI hints `x-crewquarters-widget` and `x-crewquarters-group`.

The fake's control API uses the same paths and shapes as the real one for the operations it serves,
so Person 4 can develop against either.

## 23.8 Evidence package

| Tag | Item | Owner | Evidence | Status |
| --- | --- | --- | --- | --- |
| Must | Test report with commit/image/model digests | Person 5 (Person 2 for model digests) | `make evidence` → `evidence/<UTC>/report.md` | Partial: model digests pending |
| Must | GB10 hardware/software inventory and benchmark report | Person 2 | — | Pending |
| Should | Screenshots or recording of the complete demo flow | Person 4, Person 5 | — | Pending |
| Must | Failure-injection results and known limitations | Person 5 (agents), all | Fake fault-injection tests (`test_caller.py`, `test_broker_core.py`, `test_faults.py`); limitations below | Partial |
| Must | Security checklist and unresolved-risk signoff | Person 3 | — | Pending |
| Must | Step-by-step reset and rehearsal instructions used successfully by a non-author | Person 5 | `docs/demo/operator-script.md`, `make demo-reset` | Partial: needs a non-author rehearsal |

## Known limitations (Person 5 scope)

- The SDK-to-broker API (`broker-sdk.openapi.yaml`) is a draft until Person 3 adopts it. Its run,
  input, and action operations mirror the control plane's `/internal/v1` API (checked by tests), but
  the SDK has not yet run against a real broker.
- Verified against the real control plane (2026-09-25, local Compose): `crewctl publish` imports
  the three digest-pinned agents, they install with exact permission approval, the derived
  capabilities match the fake's, and runs complete on the scheduler's fake runtime with valid run
  events. The fake runtime does not execute agent code.
- Fake-only coverage: Gmail/Sheets/Twilio are simulated. The fake LLM is rule-based unless it is
  pointed at an OpenAI-compatible server.
- There is no durable suspend/resume. A waiting agent keeps its container, and a platform restart
  interrupts the run (by design for v1, `PLAN.md` §11).
- `sheetWrite: pending_retry` is part of the result contract, but the caller retries writes inline,
  so today it reports only `written` or `failed`.
- Image vulnerability scans and SBOMs are not generated yet (Stage 6).
