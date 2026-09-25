# Person 5 — SDK, crewctl, agents, fakes, and QA: Implementation Plan

> **Update 2026-09-25:** the contract drafts this document describes (manifest, capabilities,
> run events, control API) were replaced by Person 1's canonical contracts when the work was
> integrated with the control plane. The current state and the remaining open questions are in
> [docs/decisions/0001](../../decisions/0001-person5-contract-drafts.md).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Person 5's complete scope: the `crewquarters-sdk`, `crewctl`, three agents (contract-probe, Gmail digest, caller), the fake platform and launchers, laptop Compose, the unit/contract/integration/E2E suites, CI, and demo/QA deliverables.

**Architecture:** A uv workspace of small packages. Draft contracts live in `packages/contracts` together with the shared manifest-validation code. The SDK talks HTTP to the broker API. One FastAPI fake platform implements the draft broker and control-API slice with mock providers. Sync launchers run agents as local processes or as hardened Docker containers. Agents depend only on the SDK.

**Tech Stack:** Python 3.12, uv workspace, hatchling, httpx, Pydantic v2, jsonschema, PyYAML, click, FastAPI, uvicorn, pytest, pytest-asyncio, ruff, mypy, Docker Compose, docker buildx, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-24-person5-sdk-agents-qa-design.md`. Executors read the spec for every contract detail (endpoint shapes, error codes, config fields, and algorithms). This plan does not repeat them.

**Execution note:** the user asked for development to be completed end-to-end in this session. Execution is native (superpowers:executing-plans) by the author of the spec, so each task below names its files, interfaces, and test cases, and the code lands directly in the task's commit rather than being duplicated here. A fresh reviewer checks the whole branch at the end.

## Global Constraints

- `requires-python = ">=3.12"`; every package builds with hatchling; import names are `crewquarters_contracts`, `crewquarters`, `crewctl`, `crewquarters_fake`, `contract_probe`, `gmail_digest`, `caller_agent`.
- Contract protocol string is `v1alpha1`; manifest `apiVersion` is `crewquarters/v1alpha1`.
- Every contract file carries `x-status: draft`, `x-owner`, and `x-drafted-by: Person 5`.
- Agent images are pure Python, run as user `10001:10001`, work on a read-only root filesystem with `/tmp` tmpfs, and are based on `python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9`.
- The registry image is `registry:2@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373`.
- The SDK never retries a non-idempotent operation after the request may have been sent; it raises `OutcomeUnknown`.
- Full phone numbers never appear in events, logs, results, sheets, or LLM prompts; the mask format is `••••` plus the last four digits.
- Directory names avoid the unanchored `.gitignore` patterns (`models/`, `data/`, `var/`, `build/`, `dist/`, `logs/`, `tmp/`, `token*.json`).
- Pytest runs with `--import-mode=importlib` and `asyncio_mode = "auto"`. The `e2e` and `live` markers are excluded from `make test`.
- ruff line length is 110; mypy `--strict` covers `packages/contracts/src`, `packages/python_sdk/src`, and `packages/crewctl/src`.
- Commits are small, one per task or sub-task, and end with the attribution line from the session instructions.

## Review Focus

1. **Gmail messages with a non-UTF-8 charset, an invalid `internalDate`, or no `Date` header.** The parser must return text and a best-effort `internal_date` (falling back to the `Date` header, then `None`), and the digest must still list the message. The test lives in Task 7 (`test_mime.py::test_latin1_and_missing_internal_date`) and Task 10 (`test_digest_agent.py::test_message_without_date_still_listed`).
2. **An `inputRange` that includes the header row** (`Contacts!A1:D`). The header row must be skipped with reason `consent` and never called. Test: Task 11 `test_rows.py::test_header_row_is_skipped_not_called`.
3. **Agent results that are not JSON-native** (a `datetime` in a dict result). The SDK must serialize them with `pydantic_core.to_jsonable_python` and must not crash in `serve()`. Test: Task 4 `test_agent_lifecycle.py::test_dict_result_with_datetime_is_serialized`.
4. **The broker is unreachable when the agent starts.** `serve()` must exit 2 with a one-line stderr message, not a traceback. Test: Task 4 `test_agent_lifecycle.py::test_unreachable_broker_exits_2`.
5. **Legacy IANA aliases versus abbreviations.** `Asia/Calcutta` and `UTC` are accepted; `IST`, `EST`, and `EST5EDT` are rejected with a clear validation error. Test: Task 10 `test_config.py::test_timezone_aliases_and_abbreviations`.

---

### Task 1: Workspace, tooling, and CI skeleton

**Files:**
- Create: `pyproject.toml`, `Makefile`, `conftest.py`, `.dockerignore`, `.github/workflows/ci.yml` (lint + test jobs; e2e and arm64 jobs are added in Task 12)
- Modify: `.gitignore` (add `/evidence/`, `.venv` already covered)

**Interfaces:**
- Produces: `make lint`, `make fmt`, `make test`, `make test-unit`, `make test-contract`, and `make test-integration`, all backed by `uv run`. `uv sync --all-packages` installs every workspace member.

- [ ] Write root `pyproject.toml`. It is a virtual workspace (`[tool.uv] package = false`) with all seven members, a `dev` dependency group (pytest, pytest-asyncio, ruff, mypy, types-PyYAML, openapi-spec-validator, jsonschema), ruff/mypy/pytest configuration, and the markers `e2e` and `live`.
- [ ] Write the `Makefile` with the targets above plus placeholders that later tasks fill: `dev-up`, `dev-down`, `e2e`, `images`, `demo-seed`, `demo-reset`, `demo-run`, and `evidence`. Each placeholder prints "added in Task N" until then.
- [ ] Write the CI workflow `lint` and `test` jobs using `astral-sh/setup-uv`.
- [ ] Commit.

### Task 2: Draft contracts and the shared manifest library

**Files:**
- Create: `packages/contracts/{pyproject.toml,README.md,agent-manifest.schema.json,capabilities.yaml,broker-sdk.openapi.yaml,openapi.yaml,events/run-event.schema.json}`
- Create: `packages/contracts/src/crewquarters_contracts/{__init__.py,loader.py,manifest.py,profiles.py,py.typed}`
- Test: `packages/contracts/tests/{test_loader.py,test_manifest.py,test_profiles.py}`, `tests/contract/test_contract_files.py`

**Interfaces:**
- Produces, in `crewquarters_contracts.loader`: `manifest_schema() -> dict`, `capabilities() -> dict`, `broker_openapi() -> dict`, `control_openapi() -> dict`, `run_event_schema() -> dict`, and `contracts_dir() -> Path`.
- Produces, in `crewquarters_contracts.manifest`:
  - `Issue(path: str, message: str)` (frozen dataclass).
  - `load_manifest(path: Path) -> dict`.
  - `schema_issues(manifest: dict) -> list[Issue]`.
  - `semantic_issues(manifest: dict, *, allow_unbuilt: bool) -> list[Issue]`.
  - `validate_manifest(manifest, *, allow_unbuilt=False) -> list[Issue]`.
  - `derive_capabilities(permissions: dict) -> frozenset[str]`.
  - `image_digest(image: str) -> str | None`.
  - `apply_config_defaults(schema: dict, config: dict) -> dict`.
  - `config_issues(schema: dict, config: dict) -> list[Issue]`.
  - `widget_values(schema: dict, config: dict, widget: str) -> list[str]`.
- Produces, in `crewquarters_contracts.profiles`:
  - `is_family(name) -> bool`.
  - `family_of(name) -> str`.
  - `resolve_profiles(approved: list[str], model_profile: str | None) -> list[str]`, which raises `ValueError`.
  - `FAMILY_DEFAULT_VARIANT = "small"`.

- [ ] Write the failing tests:
  - Valid and invalid manifests: extra key rejected; `REQUIRED_DIGEST` rejected unless `allow_unbuilt`; cloud profile without provider; provider without profile; `maxInputWaitSeconds > 0` with `userInput: false`; an invalid `configurationSchema`; a default that violates its own schema.
  - Capability derivation for every row of spec §4.2.
  - Profile resolution: family → `.small`; family plus a `modelProfile` in the family → that variant; `modelProfile` outside the family → `ValueError`; exact variant → itself; cloud → itself.
  - `tests/contract/test_contract_files.py`: every file parses and carries the draft markers; the manifest schema passes `Draft202012Validator.check_schema`; both OpenAPI files pass `openapi_spec_validator.validate`.
- [ ] Run them and see them fail.
- [ ] Write the contract files per spec §4 and the library.
- [ ] Run `uv run pytest packages/contracts tests/contract -q` and see them pass.
- [ ] Commit.

### Task 3: SDK foundation — errors, transport, redaction, untrusted content

**Files:**
- Create: `packages/python_sdk/{pyproject.toml,README.md}`
- Create: `packages/python_sdk/src/crewquarters/{__init__.py,py.typed,errors.py,_transport.py,redact.py,untrusted.py}`
- Test: `packages/python_sdk/tests/{test_errors.py,test_transport.py,test_redact.py,test_untrusted.py}`

**Interfaces:**
- Produces:
  - `crewquarters.__version__ = "0.1.0"` and `crewquarters.PROTOCOL = "v1alpha1"`.
  - The error classes of spec §5.4. `Cancelled` subclasses `asyncio.CancelledError` and carries `code = "RUN_CANCELLED"`. Every other class subclasses `PlatformError(message, *, code=None, request_id=None, retryable=None, details=None)`.
  - `error_from_response(status: int, body: object, request_id: str) -> BaseException`.
- Produces `BrokerClient(base_url, token, *, http: httpx.AsyncClient | None = None, max_attempts=4, sleep=asyncio.sleep)` with:
  - `async request(method, path, *, operation: str, idempotent: bool, json=None, params=None, timeout: float | None = None) -> Any`;
  - `stream_sse(path, *, operation, json, timeout) -> AsyncIterator[tuple[str, dict]]`;
  - `async aclose()`.
- Produces `redact.mask_phone(number) -> str`, `redact.redact_text(text) -> str`, and `redact.redact_value(value) -> Any`.
- Produces `untrusted.new_boundary() -> str`, `untrusted.evidence(text, *, ref, source, boundary) -> str`, `untrusted.parse_evidence(text) -> list[EvidenceBlock(ref, source, body)]`, and `untrusted.GUARD_INSTRUCTIONS: str`.

- [ ] Write the failing tests, using `httpx.MockTransport` with a recorded `sleep`:
  - A 503 on an idempotent call retries, then succeeds.
  - A 503 on a non-idempotent call raises `ProviderError` after exactly one request.
  - A 504 on a non-idempotent call raises `OutcomeUnknown`.
  - `ReadTimeout` on an idempotent call retries; on a non-idempotent call it raises `OutcomeUnknown`.
  - `ConnectError` on a non-idempotent call retries because the request was never sent.
  - A 429 with `Retry-After: 3` sleeps 3.
  - A `MODEL_UNAVAILABLE` 503 is not retried.
  - Every code in spec §5.4 maps to the right class.
  - Each call carries a fresh `X-Request-Id` and the bearer token.
  - Exhausted retries raise with `retryable=True`.
  - Redaction masks `+14155550123`, `Bearer abc.def`, and `token=xyz`, and leaves the epoch `1727136000` untouched.
  - Evidence wrapping strips the boundary from the body and `parse_evidence` round-trips it.
- [ ] Run and see them fail; implement; run and see them pass. `uv run mypy packages/python_sdk/src` is clean.
- [ ] Commit.

### Task 4: SDK run lifecycle — Agent, RunContext, events, input, idempotency

**Files:**
- Create: `packages/python_sdk/src/crewquarters/{agent.py,context.py,events.py,input.py,idempotency.py}`
- Test: `packages/python_sdk/tests/{fakebroker.py,test_events.py,test_input.py,test_idempotency.py,test_agent_lifecycle.py}`

**Interfaces:**
- Consumes: Task 3 transport and errors.
- Produces:
  - `Agent(id, *, config_model=None, result_model=None)` with `.run(fn)`, `.serve(argv=None) -> NoReturn`, and `async .execute(*, broker_url, token, run_id, http=None, install_signal_handlers=False) -> int`.
  - `RunContext` with attributes `run: RunInfo`, `config`, `capabilities: frozenset[str]`, `grants: Grants`, `limits: Limits`, `events`, `input`, `llm`, `knowledge`, `google`, `telephony`, `idempotency`.
  - The connector attributes are wired in Task 7; until then they raise `NotImplementedError`.
  - `RunInfo(id, attempt, trigger, scheduled_for, installation_id, agent_id, agent_version, created_at)`.
  - `Grants(llm_profiles, knowledge_base_ids, google, twilio, cloud_providers)`.
  - `Limits(active_timeout_seconds, input_wait_remaining_seconds)`.
  - `EventsClient` with async `log(level, message, **fields)`, `progress(percent, message, step=None)`, `metric(name, value, unit=None)`, `artifact(name, media_type, summary=None, size_bytes=None)`, `flush()`, `start()`, and `aclose()`.
  - `InputClient.ask(...)` per spec §5.1, plus `Choice(value, label=None, style="secondary")`, `InputAnswer(data, value, answered_at, answered_by)`, and the preview helpers `text_block(text)`, `key_value_block(items)`, `table_block(columns, rows)`.
  - `IdempotencyClient` with `claim(key, *, takeover=False)`, `complete(key, result)`, `get(key)`, and `once(key, fn, *, result_type=None, resume_in_progress=False)`.
  - `IdempotencyRecord(key, state, result, claimed_by_attempt, completed_at)`.
- `fakebroker.py` is an in-test ASGI app (a tiny Starlette app) that scripts handshake, heartbeat, events, result, input, and idempotency responses for lifecycle unit tests.

- [ ] Write the failing tests:
  - A successful run posts `succeeded` and exits 0.
  - An exception posts `failed` with a redacted message and exits 1.
  - A config that fails `config_model` posts `CONFIG_INVALID` without calling `fn`.
  - Heartbeat `cancelRequested` cancels the task, posts `cancelled`, and exits 1.
  - An agent raising `Cancelled` posts `cancelled`.
  - `test_dict_result_with_datetime_is_serialized`.
  - `test_unreachable_broker_exits_2`.
  - `--self-check` prints JSON and exits 0 without env variables.
  - Missing env variables exit 2.
  - Events are batched at 50, carry client event ids, and are flushed before the result.
  - `ask` with `choices` builds the spec schema and returns `value`; `expired` raises `InputTimeout`; `cancelled` raises `Cancelled`; a timeout above the budget raises `InvalidInput` before any request.
  - `once` covers `claimed` → runs `fn` and completes; `completed` → no call; `in_progress` → `OutcomeUnknown`, or a takeover with `resume_in_progress`; `result_type` round-trip.
- [ ] Run and see them fail; implement; run and see them pass; mypy is clean.
- [ ] Commit.

### Task 5: Fake platform core — store, state machine, control, broker core, admin, launchers

**Files:**
- Create: `packages/fake_platform/{pyproject.toml,README.md}`
- Create: `packages/fake_platform/src/crewquarters_fake/{__init__.py,app.py,settings.py,errors.py,store.py,statemachine.py,faults.py,control.py,admin.py,launcher.py,client.py,harness.py,server.py,testing.py}`
- Create: `packages/fake_platform/src/crewquarters_fake/broker/{__init__.py,auth.py,lifecycle.py,input.py,idempotency.py}`
- Test: `packages/fake_platform/tests/{test_statemachine.py,test_control.py,test_broker_core.py,test_faults.py}`, `tests/integration/agents/hello_agent/…` (a minimal test agent), and `tests/integration/test_hello_agent.py`

**Interfaces:**
- Produces:
  - `create_app(settings: FakeSettings | None = None) -> FastAPI`, whose `app.state.store` is a `Store`.
  - `FakeSettings.from_env()` with fields `heartbeat_seconds`, `scenarios_dir`, `llm_base_url`, `llm_model`, `llm_api_key`, and `record_traffic`.
  - `statemachine.TRANSITIONS: dict[str, frozenset[str]]`, `ACTIVE`, `TERMINAL`, and `transition(run, to, reason) -> None`.
  - `FaultRegistry` with `add(target, mode, count=1, status=None, code=None, delay_ms=0)`, `clear()`, and `async run(target, fn)`.
  - `server.BackgroundServer(app, host="127.0.0.1", port=0)`: a context manager exposing `.url`.
- Produces `client.FakePlatformClient(base_url)`, a sync httpx client with:
  - `reset()`, `load_scenario(path)`, `register_manifest(manifest)`, `import_manifest(manifest)`;
  - `install(agent_id, version, config, approved_permissions=None) -> dict`;
  - `create_run(installation_id, trigger="manual", scheduled_for=None, idempotency_key=None) -> dict`;
  - `dispatch(run_id, broker_url) -> dict` (`{env, image, entrypoint, resources, attempt}`);
  - `report_exit(run_id, attempt, exit_code)`, `get_run(run_id)`, `events(run_id, after=0)`;
  - `input_requests(state=None)`, `answer(request_id, version, data)`, `cancel(run_id)`, `retry(run_id)`;
  - `add_fault(**kw)`, `clear_faults()`, `state(kind)`, `add_auto_answer(key_pattern, data, delay_seconds=0)`;
  - `wait_for(predicate, timeout, interval=0.05)`.
- Produces the launchers:
  - `ProcessLauncher(agent_dir: Path | None, entrypoint: list[str], log_dir: Path)`, with `.broker_url_for(fake_url)` and `.start(dispatch: dict) -> LaunchHandle`.
  - `DockerLauncher(network="crewq-agents", broker_url="http://broker:8080", log_dir)`, with `.start(dispatch) -> LaunchHandle`.
  - `LaunchHandle` with `.wait(timeout) -> int`, `.kill()`, and `.log_text() -> str`.
- Produces the harness: `harness.RunOutcome(run, events, exit_code, log)` and `harness.run_agent(client, launcher, installation_id, *, trigger="manual", scheduled_for=None, timeout=60.0, run_id=None, on_launch=None) -> RunOutcome`. When `run_id` is given it re-dispatches an existing (retried) run.
- Produces the pytest fixtures in `crewquarters_fake.testing`: `fake_server` (a fresh app and `BackgroundServer` per test) and `fake_client`. The root `conftest.py` registers them via `pytest_plugins`.

- [ ] Write the failing tests:
  - The transition table equals `PLAN.md` §7.2.
  - The control flow: import → install (defaults applied, config validated, 422 on bad config, profiles resolved) → create run (idempotency-key dedupe) → dispatch → handshake → `RUNNING` → result → `SUCCEEDED`.
  - A cancel while queued goes to `CANCELLED`; a cancel while running goes to `CANCELLING`, heartbeats report `cancelRequested`, and capability ops get `RUN_CANCELLED`.
  - An exit without an outcome goes to `INTERRUPTED`, a retry goes to `QUEUED`, and the next dispatch is attempt 2 with the old token rejected.
  - Input covers create, same-key return, different-content 409, answer version conflict 409, schema-invalid 422, a long-poll that returns on answer, expiry, auto-answer, and the budget.
  - Idempotency covers claim, complete, and takeover semantics.
  - An undeclared capability gets a 403 plus a `capability.denied` event.
  - Faults cover `error`, `delay`, and `apply-then-drop`.
  - Integration: `hello_agent` runs through `ProcessLauncher` and `run_agent`, asks one input that is auto-answered, and succeeds.
- [ ] Run and see them fail; implement; run and see them pass.
- [ ] Commit.

### Task 6: Fake providers, gateway, knowledge, scenarios

**Files:**
- Create: `packages/fake_platform/src/crewquarters_fake/{gateway.py,llm_rules.py,knowledge.py,scenario.py,mailbox.py}`
- Create: `packages/fake_platform/src/crewquarters_fake/providers/{__init__.py,a1.py,gmail.py,sheets.py,twilio.py}`
- Create: `packages/fake_platform/src/crewquarters_fake/broker/{llm.py,knowledge.py,google.py,telephony.py}`
- Test: `packages/fake_platform/tests/{test_a1.py,test_gmail_provider.py,test_sheets_provider.py,test_twilio_provider.py,test_llm_rules.py,test_knowledge.py,test_scenario.py,test_broker_connectors.py}`, `tests/contract/test_route_parity.py` (the fake's `/internal/v1/sdk` and `/api/v1` routes equal the OpenAPI path+method sets)

**Interfaces:**
- Produces the providers:
  - `a1.parse_range(a1) -> A1Range(tab, start_col, start_row, end_col, end_row)`.
  - `GmailProvider` with `load(messages)`, `list(q, max_results, page_token, label_ids) -> dict`, and `get(id) -> dict`. A bad `q` raises `ValueError`.
  - `SheetsProvider` with `load(spreadsheets)`, `get(id, range)`, `update(id, range, values)`, `append(id, range, values)`, and `snapshot()`.
  - `TwilioProvider` with `load(outcomes)`, `create(run_id, to, script, gather, key) -> dict`, `get(call_id) -> dict`, and `calls_by_number() -> dict[str, int]`.
- Produces `mailbox.build_message(spec: dict, tz: ZoneInfo, now: datetime) -> dict`, which returns a Gmail `format=full` message.
- Produces `llm_rules.RuleSet.from_yaml(path)` with `.respond(messages, response_schema) -> tuple[str, dict | None]`, and `llm_rules.minimal_instance(schema) -> object`.
- Produces `gateway.Gateway(settings)` with `.profile(name) -> ProfileInfo(locality, provider, model, backend)` and `async .chat(run, request) -> dict`.
- Produces `knowledge.KnowledgeIndex` with `load_dir(kb_id, path)` and `.search(kb_id, query, top_k, document_ids) -> list[dict]`.
- Produces `scenario.load(store, path: Path, now=None)`, which applies spec §6.4.

- [ ] Write the failing tests:
  - A1 ranges: quoted tabs; `A:H`; `A2:D`; `A5:H5`; update at a row; append after the last non-empty row.
  - Gmail `q` grammar: `after`/`before` bounds, excluded categories, labels, unknown token → error; pagination is 100 per page with a stable `pageToken`; newest first.
  - Twilio progression per outcome, one call per idempotency key, and an unknown number → `failed` with `unverified-number`.
  - LLM rules: `perEvidence` emits an item per evidence block with overrides; with no match, the minimal instance validates against the schema.
  - Knowledge: BM25 ranking and document-id filtering.
  - Scenario load: relative dates resolve in the scenario timezone.
  - Broker connector routes: capability gating, `NEEDS_CONNECTION` when Google is expired, cold start drives `LOADING_MODEL`, the LLM idempotency-key cache, streaming deltas plus `done`, and `connector.call`/`llm.call` events with no payloads.
- [ ] Run and see them fail; implement; run and see them pass.
- [ ] Commit.

### Task 7: SDK connectors — LLM, knowledge, Gmail + MIME, Sheets, telephony

**Files:**
- Create: `packages/python_sdk/src/crewquarters/{llm.py,knowledge.py,telephony.py}`
- Create: `packages/python_sdk/src/crewquarters/google/{__init__.py,gmail.py,mime.py,sheets.py}`
- Modify: `packages/python_sdk/src/crewquarters/context.py` (wire the clients)
- Test: `packages/python_sdk/tests/{test_llm.py,test_knowledge.py,test_mime.py,test_gmail.py,test_sheets.py,test_telephony.py}`

**Interfaces:**
- Produces the LLM client:
  - `LLMClient.chat(profile, messages, *, temperature=None, max_output_tokens=None, response_schema=None, response_model=None, idempotency_key=None) -> ChatResult`.
  - `LLMClient.stream(...) -> ChatStream`, an async iterator of `str` with `.result`.
  - `LLMClient.resolve_profile(profile) -> str`.
  - `ChatResult(text, structured, parsed, usage: Usage(input_tokens, output_tokens), finish_reason, provider, model, locality, latency_ms, request_id)`.
- Produces the knowledge client: `KnowledgeClient.search(...) -> SearchResult(passages: list[Passage])` with `.as_context(boundary=None)`, and `Passage(citation_id, text, score, document_id, document_name, locator)`.
- Produces the Gmail parsing API:
  - `mime.GmailMessage(id, thread_id, label_ids, snippet, internal_date, headers, sender, subject, text_body, truncated_body, web_link)`;
  - `mime.parse_message(raw, *, max_chars=4000) -> GmailMessage`;
  - `mime.html_to_text(html) -> str` and `mime.strip_quoted_replies(text) -> str`.
- Produces `GmailClient.list_message_ids(query, *, max_results=100, page_token=None, label_ids=None) -> MessageIdPage(ids: list[tuple[str, str]], next_page_token, result_size_estimate)`, `GmailClient.iter_message_ids(query, *, limit) -> IdIteration`, and `GmailClient.get_message(id, *, max_chars=4000) -> GmailMessage`. `IdIteration` is an async iterator with a `.truncated` attribute that is set when iteration stops at `limit` while more ids exist.
- Produces `SheetsClient.get_values(spreadsheet_id, range_) -> list[list[str]]`, plus `update_values(...)` and `append_values(...)`, both returning `UpdateResult(updated_range, updated_rows)`.
- Produces `telephony.is_e164(s) -> bool`, `TelephonyClient.create_call(to, *, disclosure, script, gather_seconds, idempotency_key) -> Call`, `get_call(id) -> Call`, `wait_for_call(id, *, timeout_seconds, poll_seconds=2.0) -> Call`, and `TERMINAL_STATES`.

- [ ] Write the failing tests:
  - Family resolution: one match, zero matches, several matches.
  - An explicit cloud profile is sent as-is; a local family never resolves to cloud.
  - `response_model` parse failure raises `InvalidInput` carrying the raw text.
  - The stream yields deltas and the final result.
  - MIME: `text/plain` preferred; HTML-only with script, style, and comments removed; nested multipart at depth 3; attachment-only yields an empty string; malformed base64 without padding; a latin-1 charset; `test_latin1_and_missing_internal_date`; an oversize body is truncated; a depth bomb (depth 50) does not recurse past 10; quoted-reply stripping keeps the original.
  - `iter_message_ids` crosses pages and sets `truncated` at the limit.
  - `append_values` with a 504 raises `OutcomeUnknown` after one request.
  - Invalid E.164 raises `InvalidInput` before any request.
  - `wait_for_call` returns on a terminal state, or returns the last state at the timeout.
- [ ] Run and see them fail; implement; run and see them pass; mypy is clean.
- [ ] Commit.

### Task 8: crewctl

**Files:**
- Create: `packages/crewctl/{pyproject.toml,README.md}`
- Create: `packages/crewctl/src/crewctl/{__init__.py,cli.py,validate.py,scaffold.py,templates.py,testing.py,build.py,publish.py,py.typed}`
- Test: `packages/crewctl/tests/{test_validate_cmd.py,test_init_cmd.py,test_build_rewrite.py,test_test_cmd.py,test_publish_cmd.py}`

**Interfaces:**
- Consumes: `crewquarters_contracts.manifest`, `crewquarters_fake.{create_app,BackgroundServer,FakePlatformClient,ProcessLauncher,DockerLauncher,harness.run_agent}`.
- Produces: the console script `crewctl`, with `build.rewrite_image_line(text: str, new_image: str) -> str` and `build.image_repo(registry, agent_id) -> str`.

- [ ] Write the failing tests with click's `CliRunner`:
  - `init` creates the documented tree, refuses a non-empty directory, and the scaffold passes `validate --allow-unbuilt`.
  - `validate` has 0/1 exit codes and `--json` shape.
  - `rewrite_image_line` changes only the `image:` line and keeps comments.
  - `test` on the freshly `init`-ed agent succeeds in process mode and prints its timeline.
  - `publish` sends an import to a `BackgroundServer` fake and rejects `--target public`.
- [ ] Run and see them fail; implement; run and see them pass; mypy is clean.
- [ ] Commit.

### Task 9: contract-probe agent

**Files:**
- Create: `agents/contract_probe/{manifest.yaml,pyproject.toml,Dockerfile,README.md}`
- Create: `agents/contract_probe/src/contract_probe/{__init__.py,__main__.py,agent.py,checks.py,models.py}`
- Create: `agents/contract_probe/scenarios/default/{scenario.yaml,config.yaml,llm-rules.yaml,knowledge/…}`
- Test: `agents/contract_probe/tests/test_checks.py`, `tests/integration/test_contract_probe.py`

**Interfaces:**
- Produces: `contract_probe.agent.agent: Agent`; `ProbeConfig`; `ProbeReport(checks: list[CheckResult(name, status, detail, duration_ms)])`; `checks.REGISTRY: dict[str, Callable[[RunContext], Awaitable[CheckResult]]]`.

- [ ] Write the failing tests:
  - Unit: the result mapping, the isolation check skipped when `expectIsolation` is false, and one failing check fails the run with `CONTRACT_CHECKS_FAILED`.
  - Integration: all checks except isolation and cancellation pass against the fake.
  - Integration: a cancellation-only run is cancelled by the test after `WAITING` and ends `CANCELLED`.
  - Integration: a permission-denied event is recorded.
- [ ] Run and see them fail; implement; run and see them pass. `crewctl validate --allow-unbuilt agents/contract_probe` passes.
- [ ] Commit.

### Task 10: daily-gmail-digest agent

**Files:**
- Create: `agents/gmail_digest/{manifest.yaml,pyproject.toml,Dockerfile,README.md}`
- Create: `agents/gmail_digest/src/gmail_digest/{__init__.py,__main__.py,agent.py,config.py,window.py,query.py,prompts.py,classify.py,reduce.py,models.py}`
- Create: `agents/gmail_digest/scenarios/default/…`, `tests/fixtures/scenarios/{digest-basic,digest-volume,digest-malformed,digest-injection,digest-dst,digest-empty,digest-expired}/…`
- Test: `agents/gmail_digest/tests/{test_config.py,test_window.py,test_query.py,test_classify.py,test_reduce.py,test_digest_agent.py}`, `tests/integration/test_gmail_digest.py`

**Interfaces:**
- Produces:
  - `gmail_digest.agent.agent`.
  - `DigestConfig` (Pydantic; timezone validator).
  - `window.target_date(reference: datetime, tz: ZoneInfo, override: date | None) -> date`.
  - `window.day_window(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]` (UTC).
  - `query.build_query(start, end, exclude_categories, include_labels) -> str`.
  - `classify.DigestBatch`, `classify.BatchItem`, and `classify.classify_batch(ctx, messages, refs, *, boundary, profile) -> dict[str, BatchItem]`.
  - `reduce.build_digest(...) -> DigestResult`.
  - `models.DigestResult` and `models.DigestItem`.

- [ ] Write the failing tests:
  - Window: `America/Santiago` 2026-09-06 (midnight does not exist) starts at 01:00 local; New York 2026-03-08 is 23 h and 2026-11-01 is 25 h; Kolkata; `Australia/Lord_Howe` 2026-10-04 is a 23.5 h day; the year boundary; a scheduled reference late by 5 h still picks the day before `scheduled_for`.
  - `test_timezone_aliases_and_abbreviations`.
  - The query string exactly.
  - Classify: unknown refs dropped, missing refs → Important + `needsReview`, a repair retry, a second failure → whole batch `needsReview`.
  - Reduce: sorting and counts.
  - `test_message_without_date_still_listed`.
  - Integration scenarios: basic grouping; 150 messages with cap 120 (processed 120, truncated, 2 pages); empty → success; malformed set; injection (schema intact, the LLM log shows email text only inside evidence blocks, and no telephony or sheets calls); DST scheduled run; Google expired → `GOOGLE_RECONNECT_REQUIRED`.
- [ ] Run and see them fail; implement; run and see them pass; `crewctl validate --allow-unbuilt` passes.
- [ ] Commit.

### Task 11: caller agent

**Files:**
- Create: `agents/caller/{manifest.yaml,pyproject.toml,Dockerfile,README.md}`
- Create: `agents/caller/src/caller_agent/{__init__.py,__main__.py,agent.py,config.py,rows.py,approval.py,results.py,models.py}`
- Create: `agents/caller/scenarios/default/…`, `tests/fixtures/scenarios/{caller-basic,caller-statuses}/…`
- Test: `agents/caller/tests/{test_config.py,test_rows.py,test_approval.py,test_results.py}`, `tests/integration/test_caller.py`, `tests/contract/test_traffic_conformance.py` (runs the probe, digest, and caller with `record_traffic` on; every recorded request and response validates against the OpenAPI schemas)

**Interfaces:**
- Produces:
  - `caller_agent.agent.agent` and `CallerConfig`.
  - `rows.ContactRow(row, name, phone, consent, status)` and `rows.read_rows(values, start_row) -> list[ContactRow]`.
  - `rows.classify(rows, max_calls) -> Plan(eligible: list[ContactRow], skipped: list[Skipped(row, name, reason)])`.
  - `approval.approval_key(plan, script, disclosure) -> str` and `approval.build_request(plan, config) -> dict` (the `ask` kwargs).
  - `results.display_status(call: Call | None, timed_out: bool) -> str`, `results.row_values(...) -> list[str]`, and `results.HEADER`.
  - `models.CallerResult` and `models.RowResult`.

- [ ] Write the failing tests:
  - Row classification covers every rule in spec §8.3 rule 2, including `test_header_row_is_skipped_not_called`, duplicates, and over-cap.
  - The approval key is stable and changes when the script or a number changes.
  - The preview never contains a full number.
  - Display status mapping for each state.
  - Integration: approve → three calls and three rows; consent `no`; operator cancel → zero calls; `sheets.update` error ×2 → written with one call per number; `apply-then-drop` on `telephony.create` → one provider call; kill after the first call completes → `INTERRUPTED` → retry → no redial and all rows written; the statuses scenario shows every state; a full-number leak scan over events, logs, results, sheets, and the LLM log finds nothing.
- [ ] Run and see them fail; implement; run and see them pass; `crewctl validate --allow-unbuilt` passes.
- [ ] Commit.

### Task 12: Docker images, Compose, DockerLauncher E2E, and CI e2e/arm64 jobs

**Files:**
- Create: `packages/fake_platform/Dockerfile`, `infra/compose/compose.yaml`, `infra/compose/README.md`
- Create: `tests/e2e/{conftest.py,test_e2e_agents.py}`
- Modify: the agent Dockerfiles from Tasks 9–11 (finalise), `Makefile` (`dev-up`, `dev-down`, `images`, `e2e`), `.github/workflows/ci.yml` (`e2e` and `arm64-images` jobs)

**Interfaces:**
- Consumes: `crewctl build --push`, `DockerLauncher`, `FakePlatformClient`.
- Produces: `make dev-up`, `make e2e` (builds and pushes the host-platform images, then runs `pytest -m e2e`), and `make images` (multi-arch).

- [ ] Write the E2E tests, which skip unless the Docker stack is up:
  - Digest demo run by digest.
  - Caller approve run by digest.
  - Contract-probe with all checks including isolation, non-root, and read-only.
  - The container runs as a non-root user (`docker inspect` config user).
- [ ] `make dev-up && make e2e` passes locally on arm64. `--self-check` works for each image.
- [ ] Commit.

### Task 13: Demo, docs, decisions, release checklist, evidence

**Files:**
- Create: `tests/fixtures/scenarios/demo/…`, `infra/scripts/{demo-seed.sh,demo-reset.sh,collect-evidence.sh}`
- Create: `docs/demo/operator-script.md`, `docs/release/checklist.md`, `docs/sdk/{quickstart.md,reference.md,idempotency.md,untrusted-content.md}`, `docs/decisions/0001-person5-contract-drafts.md`
- Modify: `Makefile` (`demo-seed`, `demo-reset`, `demo-run`, `evidence`), `README.md` (a short "Developer quickstart" section pointing to the docs)
- Create: `tests/live/test_live_platform.py` (marked `live`; skipped unless `CREWQ_LIVE_PLATFORM_URL` is set; runs contract-probe, then the digest and caller against a real platform, using installation ids from `CREWQ_LIVE_*` variables)
- Test: `tests/integration/test_demo_scenario.py`, which runs both agents against the demo scenario in process mode.

- [ ] Write the failing demo-scenario test; build the scenario; pass.
- [ ] Write the scripts and docs. Run `make demo-seed`, `make demo-run AGENT=gmail_digest`, and `make evidence` against the running stack.
- [ ] Commit.

### Task 14: Final verification and whole-branch review

- [ ] `make lint && make test && make dev-up && make e2e && make evidence`, then fix anything red.
- [ ] Dispatch one fresh reviewer over the whole branch diff against the spec; fix confirmed findings.
- [ ] Commit, and summarise the results with evidence.
