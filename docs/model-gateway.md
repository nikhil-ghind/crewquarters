# Model gateway

Owner: Akshay Sunil Navani (Person 2). Package `crewquarters_gateway` (`services/model_gateway`), command `cq-gateway` (port 8090, private network only). Design: ADR 0008.

## Responsibilities

- **Catalog.** Sync `catalog/models/<profile>/*.json` into `model_catalog` at startup: `dev` (mock servers) on laptops, `dgx` (pinned vLLM plus Hugging Face commits) on the appliance.
- **Residency.** Install, load and unload models through the runtime daemon. Manage leases, admission control, idle unload and crash reconciliation (`manager.py`).
- **Inference** (`inference.py`, `adapters.py`). One normalized request and response for the local vLLM or mock server (OpenAI-compatible), OpenAI (Responses API) and Anthropic (Messages API through the official `anthropic` SDK). Streaming is done with SSE.
- **Authorization.** Runs present their capability token in `X-Capability-Token`. The gateway confirms with the control API that the run is active, that the token belongs to the current attempt (`capabilityTokenId`), and that it carries `llm.profile:<variant>`, plus `cloud.<provider>` for cloud profiles. Chat calls come only from the control API and are local-only.
- **Cloud credentials** (`credentials.py`). Decrypts the OpenAI and Anthropic keys that the owner saved in Connections, in-process, with the device keyring.
- **Budgets and usage.** A per-run token limit, an optional daily cloud-token budget, the provider profile's own budgets, one `llm_usage` row per request, and an `llm.cloud_call` audit event for every cloud request. Prompts are never logged.
- **Idempotency** (`idempotency.py`). Keyed non-streaming requests run once; duplicates wait for, or replay, the first result.

## Internal API (`/internal/v1`, service token)

| Route | Purpose |
| --- | --- |
| `GET /models`, `GET /models/{id}` | Catalog with `downloadState`, `memoryState`, `stage`, progress, reservation, leases, errors |
| `GET /models/{id}/events` | SSE `model.state` snapshots whenever the model changes |
| `POST /models/{id}/install`, `POST /models/{id}/install/cancel` | Start or resume a pinned download; cancel it, optionally clearing partial files |
| `DELETE /models/{id}/files` | Delete an installed model (it must not be resident) |
| `POST /models/{id}/load`, `POST /models/{id}/unload` | Manual load (admission applies); unload (`force` to override leases) |
| `GET /memory` | System reserve, serving cap, reservations and host free memory (feeds the top-bar popover) |
| `POST /leases`, `DELETE /leases/holders/{type}/{id}` | Chat leases (control API only) |
| `POST /llm/chat` | Normalized request; `"stream": true` returns SSE `delta` / `done` / `error` events |
| `POST /provider-profiles/{profileId}/test` | Check a stored OpenAI/Anthropic key (below) |
| `GET /metrics` | Prometheus: request counts, latency, tokens, residency, reservations |

Every response carries `X-Request-Id`: the caller's value when it is 8 to 128 printable ASCII characters, otherwise one the gateway generates. Error envelopes always carry it as `requestId`.

## Normalized request

```json
{"profile": "local.general", "messages": [{"role": "system|user|assistant", "content": "..."}],
 "maxOutputTokens": 1000, "temperature": 0.2, "responseSchema": {...}, "stream": false,
 "idempotencyKey": "digest-final-v1"}
```

- A profile is a family (`local.general`, resolved through the run's model bindings), an exact variant, or a cloud profile (`openai.<name>`, `anthropic.<name>`). Cloud model resolution is described under "Cloud credentials".
- Anthropic requests enable the API's server-side refusal fallback (`fallbacks: "default"`); turn it off with `CQ_GATEWAY_ANTHROPIC_FALLBACKS=false`. Claude Opus 5 rejects `temperature`, so the gateway drops it and lists it in `ignoredParameters`.
- Tool calling is outside the v1 common subset and returns `422 UNSUPPORTED_FEATURE`.
- The response is the broker-SDK contract's `ChatResponse` (`packages/contracts/broker-sdk.openapi.yaml`): `text`, `structured` (validated against `responseSchema`), `usage`, `finishReason` (`stop`, `length`, `content_filter` or `error`; provider values are mapped onto these), `provider`, `model`, `locality` (`local` or `cloud`), `latencyMs` and `requestId`. It also has `profile` and `ignoredParameters`. `requestId` is the provider's request ID, or the gateway request ID when the provider has none (the mock server, vLLM streams).

## Cloud credentials

The owner saves an OpenAI or Anthropic key in Connections. The capability broker encrypts it into `encrypted_secrets` and records a `provider_profiles` row (allowed models, budgets, enabled, status). Only the gateway decrypts these keys, in-process, through `crewquarters_secret_store` (PLAN.md section 10.2, ADR 0007). There is deliberately no environment-variable key path.

- **Keyring.** `CQ_MASTER_KEY_FILE` is the same keyring file the broker uses (`<version>:<64 hex>` lines; it must not be readable by "other"). Compose mounts it read-only into the broker and the gateway only. When the variable is unset, cloud providers are disabled and cloud profiles return `409 NEEDS_CONNECTION` ("the model gateway has no master key"). A configured but unreadable or invalid file stops the gateway at startup.
- **Active profile.** For each provider the gateway uses the enabled profile whose status is not `ERROR`, preferring `CONNECTED` over `UNTESTED`, then the newest. With no such profile, the call returns `409 NEEDS_CONNECTION` with `details.provider`.
- **Key handling.** Decrypted keys are cached in memory for `CQ_GATEWAY_CREDENTIAL_CACHE_SECONDS` (60 s; 0 disables the cache), so a replaced or deleted key stops being used within that window. Keys never appear in logs, error messages, audit events or object reprs. A key that fails to decrypt is logged with its profile ID only (`credentials.decrypt_failed`).
- **Models.** `<provider>.<name>` resolves in this order:
  1. The operator override `CQ_GATEWAY_OPENAI_MODELS` / `CQ_GATEWAY_ANTHROPIC_MODELS` (JSON, name to model ID).
  2. `default`: the profile's first allowed model; for Anthropic without allowed models, `CQ_GATEWAY_ANTHROPIC_DEFAULT_MODEL` (`claude-opus-5`).
  3. A name listed in the profile's `allowedModels` is that model ID, for example `openai.gpt-5` for an allowed `gpt-5`.

  Anything else returns `409 NEEDS_CONFIGURATION`. When `allowedModels` is not empty, a resolved model outside it (including an operator override) returns `403 PERMISSION_DENIED`.
- **Budgets.** The profile's `budgets` object may set `dailyTokens` (the provider's tokens per day; the smaller of this and `CQ_GATEWAY_DAILY_CLOUD_TOKEN_BUDGET` applies) and `perRunTokens` (one run's tokens with this provider). Both are checked before the call against recorded usage plus in-flight reservations, like the gateway-wide limits. They fail with `429 CLOUD_BUDGET_EXCEEDED` or `429 RUN_TOKEN_BUDGET_EXCEEDED`, with `details.limit`. Other keys, and values that are not positive integers, are ignored.

### Connection test

`POST /internal/v1/provider-profiles/{profileId}/test` (service token). The control API calls it when the owner presses **Test**.

```json
200 {"status": "CONNECTED" | "ERROR", "detail": "The provider rejected the key." | null, "checkedAt": "2026-09-25T10:00:00+00:00"}
404 {"error": {"code": "NOT_FOUND", ...}}   // unknown id, or not an OpenAI/Anthropic profile
```

- The gateway decrypts the profile's key, bypassing the cache, whether or not the profile is enabled. It makes one authenticated call with no retries and a `CQ_GATEWAY_PROVIDER_TEST_TIMEOUT_SECONDS` (15 s) timeout: `GET /v1/models` on OpenAI, `GET /v1/models?limit=1` on Anthropic. No tokens are billed.
- The result is written to `provider_profiles.status` and `last_checked_at`, and audited as `connection.<provider>.tested`.
- A definite rejection (HTTP 401/403, or a key that cannot be decrypted) stores `ERROR`, which removes the profile from routing until a later test succeeds. A transient failure (429, a provider 5xx, a network error) is reported as `ERROR` with "Try again later". It does not demote a profile that was `CONNECTED`.
- The response never contains key material or the provider's response body.

## Idempotency

The SDK retries a keyed call (`idempotency_key=`) after transport errors and 502/503/504. Without idempotency in the gateway, that retry could repeat a billed cloud call.

- **Key.** Non-streaming `/llm/chat` requests with `idempotencyKey` (at most 200 characters) are keyed by `(holderType, holderId, idempotencyKey)`. The holder is the run, or the chat session. The same key in another run is a different request.
- **Duplicates.** A duplicate that arrives while the first request runs waits for it and returns the same response. A later duplicate gets the stored response for `CQ_GATEWAY_IDEMPOTENCY_TTL_SECONDS` (1 h). At most `CQ_GATEWAY_IDEMPOTENCY_MAX_ENTRIES` (2000) completed results are kept; the oldest are dropped first, and in-flight requests are never dropped.
- **What is stored.** Successful responses, and errors raised after the provider produced (and billed) a result: `MODEL_REFUSED` and `STRUCTURED_OUTPUT_INVALID`. Errors that happen before or instead of a result (authorization, budgets, model loading, provider unavailable) are not stored, so the retry runs again. If the first request is cancelled, waiting duplicates get `503 MODEL_UNAVAILABLE` and the key is released.
- **Conflicts.** The same key with a different request body (ignoring `stream`, `holder` and the key itself) returns `422 INVALID_REQUEST`.
- **Streams** (`"stream": true`) are never replayed. A keyed stream holds its key while it runs, and any duplicate with that key, streaming or not, gets `409 REQUEST_IN_PROGRESS`.
- **Limitation.** The store is process memory: it is lost when the gateway restarts, and it is not shared between processes (the appliance runs one gateway). A durable store would need a table and a migration, which the control API owns.

## Timeouts

Each layer waits longer than the layer inside it, so the innermost timeout always fires first and produces a real error, not a hang or an `OUTCOME_UNKNOWN`:

| Layer | Waits for | Value | Where |
| --- | --- | --- | --- |
| Model gateway | Cold model to become ready | 900 s | `CQ_GATEWAY_WAIT_READY_SECONDS` (matches the dgx catalog's `startupTimeoutSeconds`; PLAN.md allows about 10 min) |
| Model gateway | One provider or model request | 300 s | `CQ_GATEWAY_REQUEST_TIMEOUT_SECONDS` |
| Capability broker | Gateway (`/llm/chat`, streams) | 1260 s | `GATEWAY_TIMEOUT_SECONDS` in `crewquarters_broker/main.py` |
| SDK | Broker (`ctx.llm.chat`, `ctx.llm.stream`) | 1320 s | `LLM_TIMEOUT_SECONDS` in `crewquarters/llm.py` |

- **Gateway worst case.** 900 s + 300 s = 1200 s, which is less than the broker's 1260 s, which is less than the SDK's 1320 s.
- **Anthropic retries.** The SDK client retries up to twice, so a request takes at most 3 × 300 s = 900 s, which fits the same bound. A cloud request never waits for a model load.
- **Changing a value.** If you raise either gateway value, raise the broker and SDK values to keep the order. `test_gateway_idempotency_contract.py::test_each_layer_waits_longer_than_the_one_inside_it` enforces it.
- **Control API.** The control API's chat client (`CQ_MODEL_GATEWAY_URL` calls, 900 s) is outside this chain. A cold-starting chat can exceed it.

## Error codes

The broker relays gateway errors to agents unchanged, so the gateway uses the broker-SDK contract's codes and statuses where one exists.

| Code | Status | Meaning |
| --- | --- | --- |
| `UNAUTHENTICATED` | 401 | Missing or bad service credential, or an invalid or expired capability token |
| `RUN_NOT_ACTIVE` | 409 | The token's run attempt is no longer the active one |
| `CAPABILITY_DENIED` | 403 | The run lacks `llm.profile:<variant>` or `cloud.<provider>`; `details.capability` |
| `PERMISSION_DENIED` | 403 | Chat asked for a cloud profile, or the model is not in the profile's `allowedModels` |
| `NEEDS_CONNECTION` | 409 | No usable provider key, or no master key |
| `NEEDS_CONFIGURATION` | 409 | No model is configured for this cloud profile name |
| `MODEL_NOT_INSTALLED` | 409 | Install the model first |
| `MODEL_CAPACITY_EXCEEDED` | 409 | Admission refused; details carry the numbers |
| `MODEL_LOAD_FAILED` / `MODEL_UNAVAILABLE` / `MODEL_LOAD_TIMEOUT` | 503 / 503 / 504 | The server exited or was unloaded, or never became healthy |
| `MODEL_UNLOADING` / `MODEL_IN_USE` | 409 | Manual drain in progress / leases held |
| `RUN_TOKEN_BUDGET_EXCEEDED` / `CLOUD_BUDGET_EXCEEDED` | 429 | Budgets; `details.limit` for profile budgets |
| `MODEL_REFUSED` | 422 | Provider refusal (Anthropic `stop_reason: refusal`) |
| `RATE_LIMITED` | 429 | The provider rate limited the request |
| `PROVIDER_ERROR` | 502 | The provider rejected the request (4xx); `details.providerStatus` |
| `PROVIDER_UNAVAILABLE` | 503 | The provider is unreachable or returned 5xx |
| `STRUCTURED_OUTPUT_INVALID` | 502 | The model's JSON did not match `responseSchema` |
| `INVALID_REQUEST` | 422 | Bad `idempotencyKey`, or a key reused for a different request |
| `REQUEST_IN_PROGRESS` | 409 | A keyed stream with this key is running |
