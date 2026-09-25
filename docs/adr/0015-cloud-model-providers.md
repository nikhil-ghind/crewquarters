# ADR 0015: Explicit, opt-in cloud model providers

- Status: Accepted
- Date: 2026-09-25
- Owner: Nikhil Sajan Khaneja (Person 3), with Akshay Sunil Navani (Person 2) for the model gateway

## Context

Crewquarters is local first, but some agents need a larger model than the device can serve. PLAN.md sections 1, 8.4 and 25 allow OpenAI and Anthropic under strict rules:

- Cloud use must never be an automatic fallback when a local model is slow, missing or full.
- Agents never receive provider keys (PLAN.md section 2.3).
- Data that leaves the device, and money spent, must be visible and bounded.
- An SDK retry must not repeat a billed call.

## Decision

The model gateway (`services/model_gateway/src/crewquarters_gateway/`) is the only service that calls OpenAI or Anthropic. It uses two adapters in `adapters.py`:

- `OpenAIAdapter` calls the Responses API (`POST /v1/responses`, `store: false`) over `httpx`.
- `AnthropicAdapter` calls the Messages API through the official `anthropic` SDK.

Both return the same normalized result: text, validated structured output, usage, finish reason, provider, model, latency and request ID. Tools are rejected with `422 UNSUPPORTED_FEATURE`. Provider error bodies are never echoed. Upstream failures map to `RATE_LIMITED` (429), `PROVIDER_ERROR` (502) or `PROVIDER_UNAVAILABLE` (503).

**Four explicit steps, no fallback.** A cloud call succeeds only if all four hold:

1. The manifest declares it. `permissions.cloudProviders` lists `openai` or `anthropic`, and `permissions.llmProfiles` lists a cloud profile such as `anthropic.default` (`packages/contracts/agent-manifest.schema.json`).
2. The owner approves it at install. The approval becomes the capabilities `cloud.<provider>` and `llm.profile:<provider>.<name>` (`crewquarters_shared/capability.py`).
3. An enabled provider profile holds a usable key. `SecretStoreCredentials.profile()` picks the enabled profile whose status is not `ERROR`, preferring `CONNECTED`, then `UNTESTED`, then the newest. Without one the call fails with `409 NEEDS_CONNECTION`.
4. The call names the cloud profile. `InferenceService._resolve()` routes on the profile's prefix. A `local.*` profile is only ever served locally. The SDK refuses an ungranted cloud profile before sending it (`crewquarters/llm.py`).

Chat from the control API is local-only (`403 PERMISSION_DENIED`). The broker checks `cloud.<provider>` before forwarding, and the gateway re-verifies the token, the active attempt and both capabilities. A missing capability returns `403 CAPABILITY_DENIED`.

**Model names.** `<provider>.<name>` resolves in `_cloud_model()`:

1. An operator override in `CQ_GATEWAY_OPENAI_MODELS` or `CQ_GATEWAY_ANTHROPIC_MODELS`.
2. For `default`: the profile's first `allowedModels` entry. For Anthropic only, if the list is empty, `CQ_GATEWAY_ANTHROPIC_DEFAULT_MODEL` (`claude-opus-5`).
3. Otherwise a name listed in `allowedModels`.

A non-empty `allowedModels` also restricts the result of steps 1 and 2. No match is `409 NEEDS_CONFIGURATION`, and a model outside the list is `403 PERMISSION_DENIED`.

**Keys.** Keys are saved through Connections (`packages/secret_store`, ADR 0007).

- The broker's `POST /internal/v1/provider-profiles` validates the request and encrypts the key into `encrypted_secrets`. It writes a `provider_profiles` row with `allowed_models`, `budgets` and `enabled`, and audits `connection.<provider>.saved`. A duplicate name for the same provider is `409 DUPLICATE_PROVIDER_PROFILE`.
- The broker never decrypts OpenAI or Anthropic keys. Only the gateway does, in-process, with `secret_db.load(..., provider=...)`. The AAD binds each ciphertext to its row, provider and owner.
- The master key (`CQ_MASTER_KEY_FILE`) is mounted read-only into the broker and the gateway only. If it is unset in the gateway, `NoCredentials` disables cloud entirely. There is no environment-variable key path.
- Decrypted keys are cached for `CQ_GATEWAY_CREDENTIAL_CACHE_SECONDS` (60 s) and never appear in logs, errors, reprs or audit rows.
- `POST /internal/v1/provider-profiles/{id}/test` makes one unbilled `GET /v1/models` call with a 15 s timeout (`CQ_GATEWAY_PROVIDER_TEST_TIMEOUT_SECONDS`). It audits `connection.<provider>.tested`. A 401 or 403 sets the profile to `ERROR` and removes it from routing.

**Budgets and usage.** `_check_budgets()` compares recorded `llm_usage` plus in-flight reservations (prompt characters / 4 + `maxOutputTokens`) against:

- `CQ_GATEWAY_PER_RUN_TOKEN_LIMIT` (200 000) for every run, local or cloud;
- the profile's `budgets.perRunTokens` (this run's tokens with this provider);
- the smaller of the profile's `budgets.dailyTokens` and `CQ_GATEWAY_DAILY_CLOUD_TOKEN_BUDGET` (default 0, which disables it).

A run over its limit gets `429 RUN_TOKEN_BUDGET_EXCEEDED`, and a provider over its daily limit gets `429 CLOUD_BUDGET_EXCEEDED`. Every request writes one `llm_usage` row: provider, model, holder, tokens, latency, outcome and request ID, with no prompt. Every cloud request, including failures, also writes an `llm.cloud_call` audit event with the model, outcome and request ID.

**Idempotency and retries.**

- The SDK retries keyed LLM calls after transport errors and 502/503/504. The gateway's `IdempotencyStore` (`idempotency.py`) keys non-streaming requests by `(holderType, holderId, idempotencyKey)`.
- A duplicate waits for, or replays, the first outcome for `CQ_GATEWAY_IDEMPOTENCY_TTL_SECONDS` (3600). At most `CQ_GATEWAY_IDEMPOTENCY_MAX_ENTRIES` (2000) results are kept.
- Billed final errors (`MODEL_REFUSED`, `STRUCTURED_OUTPUT_INVALID`) are replayed. Errors that happen before any provider result are not stored, so a retry runs again.
- The same key with a different body is `422 INVALID_REQUEST`. A duplicate of a running stream is `409 REQUEST_IN_PROGRESS`.
- `OpenAIAdapter` does not retry. `AnthropicAdapter` uses the SDK's `max_retries=2`, and each attempt is bounded by `CQ_GATEWAY_REQUEST_TIMEOUT_SECONDS` (300 s).

## Alternatives considered

- **A general model router or catalog of providers.** Deferred (PLAN.md section 1). Two adapters are enough for the demo.
- **Fall back to cloud when the local model is unavailable.** Rejected: it would send data off the device without a decision by the owner.
- **Keys in environment variables.** Rejected: keys would live in plaintext config and could not be rotated from the UI.
- **The broker decrypts cloud keys and forwards them.** Rejected: plaintext keys would cross a service boundary.

## Consequences

- An agent that wants cloud models has to request them in its manifest, which makes cloud use visible at install time. The UI labels such runs `usesCloud` from the run's permission snapshot.
- Approved prompt content does leave the device. The audit log shows that it happened, but not what was sent.
- Anthropic requests enable the API's server-side refusal fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`) unless `CQ_GATEWAY_ANTHROPIC_FALLBACKS=false`. This may answer with another Anthropic model. It is not a local-to-cloud fallback.
- Divergences from PLAN.md:
  - Budgets are token counts only. PLAN.md sections 6.1 and 13.10 mention a cost guard, and there is no currency-based limit yet.
  - The idempotency store is in memory. It is lost when the gateway restarts, so a retry after a restart can repeat a billed call. A durable store needs a table owned by the control API.
  - PLAN.md section 10.2 describes the gateway as the holder of cloud keys. In the code the broker writes (encrypts) them and the gateway reads (decrypts) them. The broker has the same master key, so the split is enforced by code (`provider=`), not by cryptography.
  - The Anthropic SDK's automatic retries also cover 429 and 5xx answers, which can in rare cases repeat a request the provider had already processed.
