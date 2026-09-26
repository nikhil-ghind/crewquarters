# SDK reference (`crewquarters`, protocol `v1alpha1`)

The wire contract is `packages/contracts/broker-sdk.openapi.yaml` (owned by Person 3, `x-status: stable` for `v1alpha1`). Its
run, input, and action operations mirror the control plane's `/internal/v1` API. This page covers
the Python surface.

## Agent and lifecycle

```python
agent = Agent("my-agent", config_model=MyConfig, result_model=MyResult)   # models are optional

@agent.run
async def run(ctx: RunContext[MyConfig]) -> MyResult: ...

agent.serve()          # reads PLATFORM_BROKER_URL, PLATFORM_RUN_TOKEN, PLATFORM_RUN_ID
```

What happens during a run:

1. **Handshake.** Gets the run metadata, configuration, capabilities, grants, and limits.
2. **Heartbeat.** Sent every `heartbeatIntervalSeconds` (10 s by default).
3. **Your function runs.**
4. **Events are flushed.**
5. **The result is posted** (`status` `succeeded` or `failed`; the first result wins).

How results are reported:

| Your function | Posted result | Exit code |
| --- | --- | --- |
| returns an object | `succeeded` with it as `result` (validated against `result_model`; `datetime` and other values are made JSON-safe; the platform checks it against the manifest's `resultSchema`) | 0 |
| returns something that is not an object (a list, a number) | `failed` with `RESULT_INVALID` | 1 |
| raises `AgentError(code=…)` / any `PlatformError` | `failed` with that code and `retryable` flag | 1 |
| raises any other exception | `failed` with `AGENT_ERROR` (message redacted) | 1 |
| is cancelled (owner cancel, SIGTERM, or the broker rejects a call with `RUN_CANCELLED`) | `failed` with `RUN_CANCELLED` (not retryable); the platform records the run as `CANCELLED` | 1 |
| the result cannot be posted | none | 2 |

Other rules:

- An invalid configuration posts `failed` with `CONFIG_INVALID`, and your code never runs.
- `python -m your_agent --self-check` prints the SDK and protocol versions without contacting a
  broker. Image smoke tests use it.
- **Cancellation:** a run cancel arrives as `asyncio.CancelledError` (`crewquarters.errors.Cancelled`
  is a subclass). Use `try/finally` for cleanup. Do not swallow `CancelledError`. It can never be
  caught by `except Exception`.

## `RunContext`

| Member | Notes |
| --- | --- |
| `ctx.run` | `id`, `attempt`, `trigger` (`manual`/`schedule`/`agent`), `scheduled_for`, `installation_id`, `agent_id`, `agent_version`, `created_at`, `parent_run_id` and `input` (set when another agent started this run; `input` is **untrusted**) |
| `ctx.config` | Your `config_model` instance (or a `dict`) |
| `ctx.capabilities`, `ctx.grants`, `ctx.limits` | What the owner approved, as capability strings from `packages/contracts/capabilities.yaml` (`user_input`, `llm.profile:local.general.small`, `google.gmail.readonly`, …). `grants.llm_profiles` holds the bound variants and `grants.model_bindings` maps each requested profile to its variant. |
| `ctx.events` | `await log(level, message, **fields)`, `progress(percent, message, step=None)`, `metric(name, value, unit=None)`, `artifact(name, media_type=None, summary=None, size_bytes=None, sha256=None)`, `flush()`. They are sent as `run.log`, `run.progress`, `run.metric`, and `run.artifact` events (`packages/contracts/events/run-event.schema.json`); optional fields you leave out are omitted. Logs are redacted (phone numbers, bearer tokens, secret query values) and batched (up to 50 events, at least every second). |
| `ctx.input.ask(key, title, prompt, *, schema= or choices=, preview=None, consequence=None, timeout_seconds)` | Returns `InputAnswer(data, value, answered_at)`. Keys are 1–128 characters of letters, digits, `_`, `.`, `:`, `-`. The same `key` returns the same request, and a retried attempt receives the stored answer. `choices` takes strings or `Choice(value, label, style)`. The preview helpers are `text_block`, `key_value_block`, and `table_block`; blocks, choices, and the consequence are sent as one `preview` object for the approval card. Answer schemas may not use `pattern`. |
| `ctx.llm.chat(profile, messages, *, temperature, max_output_tokens, response_schema, response_model, idempotency_key)` | Returns `ChatResult(text, structured, parsed, usage, finish_reason, provider, model, locality, latency_ms, request_id)`. A family (`local.general`) resolves to the single granted variant. Cloud profiles (`openai.*`, `anthropic.*`) must be named explicitly and are never a fallback. Tools are not supported here in v1alpha1 (they are on the `/openai/v1` facade, D24). |
| `ctx.llm.stream(...)` | Async iterator of text deltas. `.result` holds the final `ChatResult`. |
| `ctx.knowledge.search(kb_id, query, *, top_k=8, document_ids=None, max_context_tokens=None)` | Returns `SearchResult(passages)`. `.as_context()` wraps the passages as untrusted evidence with citation ids as refs. |
| `ctx.knowledge.connect(kb_id=None)` | A `KnowledgeBase` handle (the granted base when no id is given) with `find_files(pattern="*", *, regex=None, limit=50)` → `FileMatches(files, total, truncated)` (`pattern` is a case-insensitive glob on the file name, `regex` an extra filter applied in the SDK) and `search(query, *, files=None, top_k=8, max_context_tokens=None)` (`files` limits the search to documents matching that glob). `ctx.knowledge.find_files(kb_id, …)` is the same without a handle. See [knowledge.md](../knowledge.md). |
| `ctx.agents.start(agent_id, *, key, input=None)` | Starts another approved agent and returns `StartedRun(run_id, agent_id, installation_id, state, created)` without waiting for it. `key` makes the start idempotent. Needs `permissions.startsAgents`. See [agent-chaining.md](../agent-chaining.md). |
| `ctx.google.gmail` | `list_message_ids(query, …)`, `iter_message_ids(query, limit=…)` (sets `.truncated`), `get_message(id, max_chars=4000)` → `GmailMessage` with safe `text_body`, `sender`, `subject`, `internal_date`, `web_link` |
| `ctx.google.sheets` | `get_values`, `update_values` (idempotent, retried), `append_values` (never retried; a timeout raises `OutcomeUnknown`) |
| `ctx.telephony` | `create_call(to, *, disclosure, script, gather_seconds, idempotency_key)`, `get_call`, `wait_for_call(id, timeout_seconds, poll_seconds=2)`. Numbers must be E.164, and the full number is never logged. |
| `ctx.voice` | `dial(to, *, idempotency_key, ring_timeout_seconds=30, max_duration_seconds=300)`, `get(call_id)`, `hangup(call_id)`, each returning `VoiceCall(id, to_masked, state, room, answered_at, ended_at, duration_seconds, error_code, …)`; `.ended` is true once the call is over. `room` holds the LiveKit `url`, `name`, `token`, and `identity` for this call only, and is `None` once the call ends. Needs `sip.call.conversational`. Dialing is retried only with its idempotency key, and the full number is never logged. |
| `ctx.model_endpoint()` | `ModelEndpoint(base_url, api_key)` for OpenAI-compatible clients (LiveKit's OpenAI plugins, the `openai` package): the broker's `/openai/v1` facade, with the run token as the key. Model names are granted profile variants (`ctx.grants.model_bindings`), checked against `llm.profile:<variant>`. Chat completions (with tools and streaming), transcriptions, and speech. |
| `ctx.idempotency` | `once(key, fn, *, result_type=None, resume_in_progress=False)`, plus `claim` and `complete`. See [idempotency.md](idempotency.md). |

## Errors (`crewquarters.errors`)

| Exception | Broker codes | Retryable |
| --- | --- | --- |
| `PermissionDenied` | `CAPABILITY_DENIED`, `PERMISSION_DENIED` | no |
| `NeedsConnection` | `NEEDS_CONNECTION` (Google or Twilio connection missing or expired) | no |
| `ModelUnavailable` | `MODEL_UNAVAILABLE` | yes |
| `RateLimited` | `RATE_LIMITED` | yes |
| `InvalidInput` | `INVALID_REQUEST`, `INVALID_INPUT_SCHEMA`, `INPUT_WAIT_BUDGET_EXCEEDED`, `UNSUPPORTED_FEATURE`, `STRUCTURED_OUTPUT_INVALID` | no |
| `ProviderError` | `PROVIDER_ERROR`, `PROVIDER_UNAVAILABLE`, `TIMEOUT` | yes |
| `OutcomeUnknown` | A non-idempotent call may or may not have been applied | no (never retried automatically) |
| `InputTimeout` | The input request expired | no |
| `Cancelled` | `RUN_CANCELLED` (subclass of `asyncio.CancelledError`) | no |
| `AgentError` | Raise this yourself to fail with your own `code` | your choice |

Every exception carries `code`, `request_id`, `retryable`, and `details`.

**Transport retries.** A non-idempotent request that may have been sent is never repeated.
There are two retry policies:

- **Platform outages** (the broker or the control API restarting). A connection that could not
  be opened (refused, DNS failure, connect timeout) is retried for every operation, because
  nothing was sent. A connection that broke mid-request, and a 502/503 that means the platform
  itself is unavailable (`UPSTREAM_ERROR`, or a response without a broker error), are retried for
  idempotent operations only; a non-idempotent call whose connection broke raises
  `OutcomeUnknown`. Backoff is `min(4, 0.5·2ⁿ) + jitter`, for up to 2.5 heartbeat intervals from
  the first failure: 25 s with the platform's default 30 s heartbeat timeout (the SDK derives it
  from the handshake's `heartbeatIntervalSeconds`, clamped to 5–120 s, and uses 25 s before the
  handshake). An outage longer than that would cost the attempt its heartbeat lease anyway, and
  the call then raises `PlatformError` with `BROKER_UNAVAILABLE` (retryable).
- **Everything else retryable** (a 429 honouring `Retry-After`, a 502/503/504 from a provider,
  a read timeout): idempotent operations only, with backoff `min(8, 0.5·2ⁿ) + jitter` over at
  most 4 attempts. `MODEL_UNAVAILABLE` is not retried.

Streaming LLM calls are not retried.
