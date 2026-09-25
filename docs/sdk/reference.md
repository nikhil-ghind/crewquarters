# SDK reference (`crewquarters`, protocol `v1alpha1`)

The authoritative contract is `packages/contracts/broker-sdk.openapi.yaml`. This page covers the
Python surface.

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
5. **The outcome is posted.**

How outcomes are reported:

| Your function | Posted outcome | Exit code |
| --- | --- | --- |
| returns a value | `succeeded` (validated against `result_model`; `datetime` and other values are made JSON-safe) | 0 |
| raises `AgentError(code=…)` / any `PlatformError` | `failed` with that code and `retryable` flag | 1 |
| raises any other exception | `failed` with `AGENT_ERROR` (message redacted) | 1 |
| is cancelled (owner cancel, SIGTERM, or the broker rejects a call with `RUN_CANCELLED`) | `cancelled` | 1 |
| the outcome cannot be posted | none | 2 |

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
| `ctx.run` | `id`, `attempt`, `trigger` (`manual`/`schedule`), `scheduled_for`, `installation_id`, `agent_id`, `agent_version`, `created_at` |
| `ctx.config` | Your `config_model` instance (or a `dict`) |
| `ctx.capabilities`, `ctx.grants`, `ctx.limits` | What the owner approved. `grants.llm_profiles` holds the resolved profile variants. |
| `ctx.events` | `await log(level, message, **fields)`, `progress(percent, message, step=None)`, `metric(name, value, unit=None)`, `artifact(name, media_type, summary=None, size_bytes=None)`, `flush()`. Logs are redacted (phone numbers, bearer tokens, secret query values) and batched (up to 50 events, at least every second). |
| `ctx.input.ask(key, title, prompt, *, schema= or choices=, preview=None, consequence=None, timeout_seconds)` | Returns `InputAnswer(data, value, answered_at, answered_by)`. The same `key` returns the same request, and a retried attempt receives the stored answer. `choices` takes strings or `Choice(value, label, style)`. The preview helpers are `text_block`, `key_value_block`, and `table_block`. |
| `ctx.llm.chat(profile, messages, *, temperature, max_output_tokens, response_schema, response_model, idempotency_key)` | Returns `ChatResult(text, structured, parsed, usage, finish_reason, provider, model, locality, latency_ms, request_id)`. A family (`local.general`) resolves to the single granted variant. Cloud profiles (`cloud.openai.*`, `cloud.anthropic.*`) must be named explicitly and are never a fallback. Tools are not supported in v1alpha1. |
| `ctx.llm.stream(...)` | Async iterator of text deltas. `.result` holds the final `ChatResult`. |
| `ctx.knowledge.search(kb_id, query, *, top_k=8, document_ids=None, max_context_tokens=None)` | Returns `SearchResult(passages)`. `.as_context()` wraps the passages as untrusted evidence with citation ids as refs. |
| `ctx.google.gmail` | `list_message_ids(query, …)`, `iter_message_ids(query, limit=…)` (sets `.truncated`), `get_message(id, max_chars=4000)` → `GmailMessage` with safe `text_body`, `sender`, `subject`, `internal_date`, `web_link` |
| `ctx.google.sheets` | `get_values`, `update_values` (idempotent, retried), `append_values` (never retried; a timeout raises `OutcomeUnknown`) |
| `ctx.telephony` | `create_call(to, *, disclosure, script, gather_seconds, idempotency_key)`, `get_call`, `wait_for_call(id, timeout_seconds, poll_seconds=2)`. Numbers must be E.164, and the full number is never logged. |
| `ctx.idempotency` | `once(key, fn, *, result_type=None, resume_in_progress=False)`, plus `claim`, `complete`, `get`. See [idempotency.md](idempotency.md). |

## Errors (`crewquarters.errors`)

| Exception | Broker codes | Retryable |
| --- | --- | --- |
| `PermissionDenied` | `CAPABILITY_DENIED` | no |
| `NeedsConnection` | `NEEDS_CONNECTION` (Google or Twilio connection missing or expired) | no |
| `ModelUnavailable` | `MODEL_UNAVAILABLE` | yes |
| `RateLimited` | `RATE_LIMITED` | yes |
| `InvalidInput` | `INVALID_REQUEST`, `INPUT_KEY_CONFLICT`, `INPUT_WAIT_BUDGET_EXCEEDED`, `UNSUPPORTED_FEATURE`, `STRUCTURED_OUTPUT_INVALID` | no |
| `ProviderError` | `PROVIDER_ERROR`, `PROVIDER_UNAVAILABLE`, `TIMEOUT` | yes |
| `OutcomeUnknown` | A non-idempotent call may or may not have been applied | no (never retried automatically) |
| `InputTimeout` | The input request expired | no |
| `Cancelled` | `RUN_CANCELLED` (subclass of `asyncio.CancelledError`) | no |
| `AgentError` | Raise this yourself to fail with your own `code` | your choice |

Every exception carries `code`, `request_id`, `retryable`, and `details`.

**Transport retries.** Only idempotent operations are retried, and only after a connection error,
a 502/503/504, or a 429 (honouring `Retry-After`). Backoff is `min(8, 0.5·2ⁿ) + jitter` over at
most 4 attempts. A non-idempotent request that may have been sent is never repeated.
