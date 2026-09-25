# Model gateway

Owner: Akshay Sunil Navani (Person 2). Package `crewquarters_gateway` (`services/model_gateway`), command `cq-gateway` (port 8090, private network only). Design: ADR 0008.

## Responsibilities

- **Catalog.** Sync `catalog/models/<profile>/*.json` into `model_catalog` at startup: `dev` (mock servers) on laptops, `dgx` (pinned vLLM plus Hugging Face commits) on the appliance.
- **Residency.** Install, load and unload models through the runtime daemon. Manage leases, admission control, idle unload and crash reconciliation (`manager.py`).
- **Inference** (`inference.py`, `adapters.py`). One normalized request and response for the local vLLM or mock server (OpenAI-compatible), OpenAI (Responses API) and Anthropic (Messages API through the official `anthropic` SDK). Streaming is done with SSE.
- **Authorization.** Runs present their capability token in `X-Capability-Token`. The gateway confirms with the control API that the run is active, that the token belongs to the current attempt (`capabilityTokenId`), and that it carries `llm.profile:<variant>`, plus `cloud.<provider>` for cloud profiles. Chat calls come only from the control API and are local-only.
- **Budgets and usage.** A per-run token limit, an optional daily cloud-token budget, one `llm_usage` row per request, and an `llm.cloud_call` audit event for every cloud request. Prompts are never logged.

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
| `GET /metrics` | Prometheus: request counts, latency, tokens, residency, reservations |

## Normalized request

```json
{"profile": "local.general", "messages": [{"role": "system|user|assistant", "content": "..."}],
 "maxOutputTokens": 1000, "temperature": 0.2, "responseSchema": {...}, "stream": false}
```

- A profile is a family (`local.general`, resolved through the run's model bindings), an exact variant, or a cloud profile (`openai.<name>`, `anthropic.<name>`).
- `anthropic.default` uses `claude-opus-5` (`CQ_GATEWAY_ANTHROPIC_DEFAULT_MODEL`). Anthropic requests enable the API's server-side refusal fallback (`fallbacks: "default"`); turn it off with `CQ_GATEWAY_ANTHROPIC_FALLBACKS=false`. Claude Opus 5 rejects `temperature`, so the gateway drops it and lists it in `ignoredParameters`.
- OpenAI profile names map to model IDs through `CQ_GATEWAY_OPENAI_MODELS` (JSON). There is no default model.
- Tool calling is outside the v1 common subset and returns `422 UNSUPPORTED_FEATURE`.
- The response has `text`, `structured` (validated against `responseSchema`), `usage`, `finishReason`, `provider`, `model`, `latencyMs`, `requestId` and `ignoredParameters`.

## Cloud credentials

Provider keys belong to the secret store owned by Nikhil Sajan Khaneja (Person 3). The gateway decrypts OpenAI and Anthropic keys in-process through that shared library (ADR 0007). Until the library exists, `NoCredentials` returns `409 CLOUD_PROVIDER_NOT_CONFIGURED`. There is deliberately no environment-variable key path.

## Error codes

| Code | Meaning |
| --- | --- |
| `MODEL_NOT_INSTALLED` | Install the model first |
| `MODEL_CAPACITY_EXCEEDED` | Admission refused; details carry the numbers |
| `MODEL_LOAD_FAILED` / `MODEL_LOAD_TIMEOUT` | The server exited, or never became healthy |
| `MODEL_UNLOADING` / `MODEL_IN_USE` | Manual drain in progress / leases held |
| `PERMISSION_DENIED` / `RUN_NOT_ACTIVE` / `INVALID_CAPABILITY_TOKEN` | Authorization failures |
| `RUN_TOKEN_BUDGET_EXCEEDED` / `CLOUD_BUDGET_EXCEEDED` | Budgets |
| `MODEL_REFUSED` | Provider refusal (Anthropic `stop_reason: refusal`) |
| `PROVIDER_RATE_LIMITED` / `PROVIDER_UNAVAILABLE` / `PROVIDER_REJECTED` | Upstream failures |
| `STRUCTURED_OUTPUT_INVALID` | The model's JSON did not match `responseSchema` |
