# Capability broker

Owner: Nikhil Sajan Khaneja (Person 3). Code: `services/capability_broker` (`crewquarters_broker`) and `packages/secret_store` (`crewquarters_secret_store`).

The broker is the only service that handles Google and Twilio credentials. An agent container can reach only the broker. It gets narrow operations authorized by its run capability token. It never gets secrets, and it never gets a generic proxy.

## Surfaces

| Prefix | Caller | Authentication |
| --- | --- | --- |
| `/internal/v1/sdk/*` | Agent containers (the SDK) | `Authorization: Bearer $PLATFORM_RUN_TOKEN` |
| `/internal/v1/connections*`, `/internal/v1/provider-profiles*` | Control API (connections), on the owner's behalf | `Authorization: Bearer $CQ_INTERNAL_SERVICE_TOKEN` |
| `/api/v1/connections/google/callback` and `/api/v1/callbacks/twilio/*` | Google and Twilio, through the reverse proxy | OAuth `state` plus browser binding; Twilio signature |

The reverse proxy must forward only the two public callback groups to the broker (PLAN.md sections 4.1 and 14.3).

## Capability checks

Every `/internal/v1/sdk` call passes all of these checks (`crewquarters_broker/auth.py`):

1. **Token:** a valid HS256 signature, `aud=crewquarters-broker`, issuer, and expiry.
2. **Run state:** the control API's run view (`GET /internal/v1/runs/{id}`) shows an active state. The token's `jti` must equal `capabilityTokenId`, and its attempt and installation must match. A token from an earlier attempt is therefore revoked.
3. **Capability:** the capability is in the token **and** in the run's current approved permissions. This is an intersection, so narrowing an approval takes effect immediately.
4. **Resource:** the knowledge base and spreadsheet IDs come from the approved installation config (see `resource` in `packages/contracts/capabilities.yaml`), never from the agent's request.
5. **Connection:** the provider connection exists and has the needed scope. Otherwise the call returns `409 NEEDS_CONNECTION`.
6. **Cancellation:** once cancellation is requested, capability operations return `409 RUN_CANCELLED`; baseline operations (handshake, heartbeat, events, result, actions) keep working.

## SDK API (`/internal/v1/sdk`)

The contract is `packages/contracts/broker-sdk.openapi.yaml` (owner Person 3, drafted by Person 5). The broker serves every operation in it, and a test (`test_broker_serves_every_contract_operation`) keeps the two in step. A second test drives the broker through Person 5's real SDK clients.

The run and attempt always come from the token. JSON is camelCase. Errors use the platform envelope, with the contract's codes:

| Code | Status | Meaning |
| --- | --- | --- |
| `UNAUTHENTICATED` | 401 | The token is invalid, revoked, or for an old attempt |
| `CAPABILITY_DENIED` | 403 | The capability isn't in both the token and the current approval |
| `PERMISSION_DENIED` | 403 | The resource, script, disclosure or number isn't the approved one |
| `RUN_NOT_ACTIVE`, `RUN_CANCELLED` | 409 | The run ended, or the owner cancelled it |
| `NEEDS_CONNECTION`, `NEEDS_CONFIGURATION`, `PROTOCOL_UNSUPPORTED`, `CALL_LIMIT_REACHED` | 409 | The owner must act, or the SDK is too new |
| `INVALID_REQUEST` | 422 | The request failed validation |
| `RATE_LIMITED` | 429 | Provider rate limit |
| `PROVIDER_ERROR`, `PROVIDER_UNAVAILABLE`, `MODEL_UNAVAILABLE` | 502, 503 | Retry only idempotent operations |
| `OUTCOME_UNKNOWN` | 503 | Twilio didn't confirm a call; never retried automatically |

**Cancellation.** Once the owner cancels, capability operations (LLM, knowledge, Gmail, Sheets, telephony, input requests) return `RUN_CANCELLED`. Handshake, heartbeat, events, result and actions keep working, so the agent can report its outcome.

### How the broker applies the contract

- **Handshake.** The broker forwards the handshake to the control API, then builds the `Handshake` response from the control API's run view (`GET /internal/v1/runs/{runId}`) and the token.
  - `trigger`, `scheduledFor`, `agentId`, `agentVersion` (semver), `createdAt`, and the limits (`activeTimeoutSeconds`, remaining active seconds, `maxInputWaitSeconds`, remaining input wait) come from the run view. The token-derived fallbacks (`manual`, the token's lifetime) apply only to an older control API without those fields.
  - A `protocol` other than `v1alpha1` returns `PROTOCOL_UNSUPPORTED`.
- **Events.** A batch is forwarded whole to `POST /internal/v1/runs/{runId}/event-batches`. The control API validates every event first (type, 16 KiB payload cap, `run-event.schema.json`) and stores none if any fails; the `422` lists each rejected event in `details.rejected[]`. A unique `(run_id, clientEventId)` makes a retried batch a no-op, and `occurredAt` is kept.
- **Heartbeat and result on a finished run.** They still authenticate against the current attempt, and pass through to the control API, which answers a heartbeat with `cancelRequested: true`. Capability operations on a finished run return `409 RUN_NOT_ACTIVE`.
- **Action claims.** `X-Claim-Token` is forwarded as `claimToken`; a retry of the same `claim()` call gets `claimed` again instead of `in_doubt` (see the contract's `claimAction`).
- **Knowledge and Sheets.** The agent sends `knowledgeBaseId` or `spreadsheetId`. The broker accepts it only if it equals the installation config's `knowledgeBaseId` or `spreadsheetId`, and otherwise returns `PERMISSION_DENIED`. Sheets values are written with `valueInputOption=RAW`, so untrusted text is never evaluated as a formula.
- **Sheets ranges.** A read (`values:get`) must lie inside the config's `inputRange`, and a write (`values:update`, `values:append`) inside its `resultRange`, on the same tab. Ranges are A1 notation and must name their tab (`Contacts!A2:D`, `'My tab'!B3`). Missing bounds are open, so `Results!A:H` allows every row of columns A-H; tab names and column letters ignore case.
  - Outside the configured range: `PERMISSION_DENIED` with `details.key`. Not A1 with a tab: `INVALID_REQUEST`. The config has no such range, or an invalid one: `NEEDS_CONFIGURATION`.
  - The caller agent reads `inputRange` and writes `resultRange!A1:H1` and `resultRange!A<row>:H<row>`, which fit its defaults. Google appends below the table it finds in the range, so `resultRange` should be open-ended downwards.
  - The Gmail digest agent does not use Sheets.
- **Gmail.** `GET /google/gmail/messages` passes `labelIds` and returns `resultSizeEstimate`. `GET /google/gmail/messages/{id}` returns Gmail's `format=full` message unmodified. It's untrusted content, and the SDK parses MIME and reduces HTML to text.
- **LLM.** `/llm/chat` and `/llm/chat:stream` are forwarded to the model gateway's `POST /internal/v1/llm/chat` with the service token and the agent's token in `X-Capability-Token`. The gateway re-verifies the token and the profile.
  - The broker first requires an `llm.profile:*` capability, plus `cloud.<provider>` for cloud profiles.
  - Stream events are translated to the contract's `delta` / `done` / `error` SSE.
  - Tools are rejected.
- **Telephony.** The contract's `CallCreate` carries the script and disclosure, but the broker speaks only what the owner approved:
  - `script.text` must equal `config.script` with every `{name}` replaced by the same plain name, so a spreadsheet cell cannot add sentences to the script. A plain name:
    - is 1-40 characters and starts with a letter;
    - contains only letters (with their combining marks), spaces, apostrophes (`'` and `’`), hyphens and periods, so no digits or other punctuation;
    - has no leading or trailing space and no two separators in a row, except `. `;
    - has a period only at its end or after a one-letter initial (`J. R. Smith`, `Sam Jones Jr.`).

    A 40-character run of letters can still read as a phrase, so the operator's approval preview lists every name. The caller agent skips rows whose name fails the same rule (`skipReason: invalid_name`), and the fake platform's broker enforces the same script, disclosure and name rules.
  - `script.disclosure` must equal `config.disclosure` when one is configured. Otherwise the broker uses its own fixed disclosure.
  - Anything else returns `PERMISSION_DENIED`, and no call is placed.

  The broker builds the TwiML: the disclosure first, then the script inside a speech `<Gather>` with the requested `timeoutSeconds`. `config.maxCalls` (default 3, at most 10) caps calls per run. The cap is checked and the call row inserted under a per-run PostgreSQL advisory lock, so concurrent requests with different keys cannot exceed it. The same `idempotencyKey` returns the same call and never redials.
- **Call view.** A `Call` has `{id, idempotencyKey, toMasked, state, answered, speechCaptured, transcript, durationSeconds, errorCode, createdAt, updatedAt}`, with `state` in Twilio's vocabulary.
  - A call Twilio rejected is `failed` with `errorCode: PROVIDER_REJECTED`.
  - A call whose create response was lost is `failed` with `errorCode: OUTCOME_UNKNOWN`, and creating it again with the same key returns `OUTCOME_UNKNOWN`.
  - Twilio can call back before the broker has recorded its answer, or after that answer was lost. The callback URL carries the broker's call id, so a signed callback for a call that has no CallSid yet (still creating, or `OUTCOME_UNKNOWN`) adopts Twilio's CallSid, provided `To` (when sent) ends in the call's last four digits. The callee hears the disclosure and script as usual, the call continues to report progress, and creating it again with the same key returns it. Only the first CallSid is adopted.
  - The first transcript wins, and late or duplicate status callbacks never move a call backwards.
- **Callback hardening.** A body over 64 KiB is refused with `413` before it is read (from `Content-Length`, or while streaming). A request without a well-formed signature is rejected without decrypting anything, and the Twilio credentials used for signature checks are cached for 60 seconds. Every rejection increments `cq_broker_callback_rejections_total`. The audit log gets at most one `callback.twilio.rejected` row per reason (`missing_signature`, `malformed_signature`, `bad_signature`) per minute, whose `count` covers the rejections since the previous row.
- **Legal compliance:** nothing here makes a call legally compliant. Restrict live tests to consenting, verified team numbers (`CQ_TWILIO_ALLOWED_NUMBERS`).

## Internal API for the control API (`/internal/v1`)

| Route | Purpose |
| --- | --- |
| `GET /connections` | Status for google, twilio, openai, anthropic in the `ConnectionStatusClient` shape: `{provider, displayName, status: NOT_CONNECTED\|CONNECTED\|NEEDS_ATTENTION\|DISABLED, grantedCapabilities, lastCheckedAt, ...}` |
| `POST /connections/google/start` | `{userId, capabilities: [gmail.readonly, spreadsheets]}` → `{authorizationUrl, browserBinding}` |
| `POST /connections/google/test` | Refresh now; an expired grant becomes `NEEDS_ATTENTION` |
| `DELETE /connections/google?userId=` | Revoke at Google (best effort), then delete the connection and secret. A secret that can no longer be decrypted (for example, its master key version was removed) is deleted without revocation |
| `PUT /connections/twilio` | `{userId, accountSid, authToken, fromNumber}`: save (or replace), then validate without calling |
| `POST /connections/twilio/test` / `DELETE /connections/twilio?userId=` | Validate without calling / delete |
| `POST /connections/twilio/test-call` | `{userId, to, confirm: true}`: a live call that speaks a fixed test message. The UI must ask the owner first. At most one a minute (`429` with `Retry-After`); in live mode only allowed numbers; audited with the number masked; not tied to a run |
| `GET /metrics` | Prometheus text (below) |
| `GET/POST /provider-profiles`, `DELETE /provider-profiles/{id}?userId=` | OpenAI and Anthropic keys. The broker only encrypts them; the model gateway decrypts and tests them |

Responses never contain secret values.

**Google sign-in (browser binding).**
1. The control API calls `start`.
2. It sets `browserBinding` as a cookie: `cq_oauth_binding`, `HttpOnly`, `SameSite=Lax`, `Secure` under HTTPS, `Path=/api/v1/connections/google`, `Max-Age=600`.
3. It redirects the browser to `authorizationUrl`.

The callback requires that cookie, so an attacker cannot make the owner's browser complete the attacker's authorization. The broker then redirects the browser to `/connections/google?result=connected` or `?result=error&code=OAUTH_STATE_INVALID|OAUTH_DENIED|OAUTH_CODE_INVALID|OAUTH_SCOPE_MISSING|OAUTH_TOKEN_INVALID|...` (`OAUTH_TOKEN_INVALID`: Google returned no access token, or refused the new one).

## Google OAuth details

- **Flow:** the web-server authorization-code flow with PKCE (S256), `access_type=offline`, `prompt=consent`, and `include_granted_scopes=true`. Gmail and Sheets are consented separately and accumulate on one connection.
- **Scopes:** only `gmail.readonly` and `spreadsheets` are ever requested. No identity scopes are requested; the account label comes from the Gmail profile when Gmail is granted.
- **Redirect URI:** exactly `CQ_PUBLIC_BASE_URL` + `/api/v1/connections/google/callback`. Register this exact URI in Google Cloud.
- **State:** 256 random bits, single use, valid for 10 minutes; only its hash is kept in memory. A broker restart during sign-in means the owner has to click Connect again.
- **Tokens:** refresh tokens are envelope-encrypted, and access tokens are kept in memory only.
- **Refreshing:** one refresh at a time per connection; concurrent requests wait for it and reuse the new token. No database connection is held while Google answers (up to 20 seconds). A token Google refuses right after a refresh gives `PROVIDER_ERROR` (`providerStatus: 401`), and a refresh token that cannot be decrypted gives `NEEDS_CONNECTION`.
- **One Google account per device:** connecting again replaces the previous connection.
- **Testing mode:**
  - Google caps the test-user list and may show an "unverified app" warning.
  - Refresh tokens for these scopes **expire after seven days**. The broker then marks the connection `NEEDS_ATTENTION` and returns `NEEDS_CONNECTION` to agents. Reconnecting is an expected demo task.
  - Publishing with Gmail restricted scopes requires Google verification and possibly a security assessment.
- **Implementation choice:** the flow is written directly on `httpx` (three requests: authorize URL, code exchange, refresh) rather than through a library. The one candidate library, authlib, deprecates its httpx integration in 1.8.

## Secrets (`packages/secret_store`)

- **Encryption:** envelope encryption. Each secret gets its own random 256-bit data key, used with AES-256-GCM. The data key is wrapped with the device master key (AES-256-GCM), under the current key version.
- **Binding:** the blob header and the context `{id, provider, owner}` are bound as associated data. A ciphertext moved to another row, provider, or key version fails authentication.
- **Master keyring:** `CQ_MASTER_KEY_FILE` holds lines of `<version>:<64 hex characters>`, and the highest version encrypts new secrets. The loader refuses a file that other users can read or write.
- **Rotation:** add a new line, then call `db.replace()` to re-encrypt a secret under it.
- **Who decrypts:** the broker decrypts only Google and Twilio secrets, and the model gateway decrypts only OpenAI and Anthropic keys (`db.load(..., provider=...)`). Plaintext never travels between services.
- **Undecryptable secrets:** if a secret cannot be decrypted (say, its key version was removed from the keyring), agents get `NEEDS_CONNECTION` and the owner can still delete the connection, then connect again.
- **Development:** the `dev` profile uses a fixed, insecure development key when no file is set. Every other profile refuses to start without one.

## Metrics

`GET /internal/v1/metrics` (service token) returns Prometheus text. Labels are route templates, providers, and codes: never IDs, tokens, or phone numbers.

| Metric | Meaning |
| --- | --- |
| `cq_http_requests_total{method,route,status}`, `cq_http_request_duration_seconds` | Request volume, errors, latency |
| `cq_broker_denials_total{code}` | Refused agent calls: `UNAUTHENTICATED`, `CAPABILITY_DENIED`, `PERMISSION_DENIED`, `RUN_NOT_ACTIVE`, `RUN_CANCELLED` |
| `cq_broker_provider_requests_total{provider,outcome}`, `cq_broker_provider_request_duration_seconds{provider}` | Google and Twilio usage and latency; `outcome` is `2xx`, `4xx`, `5xx`, or `error` for transport failures |
| `cq_broker_oauth_refresh_failures_total{provider,reason}` | `invalid_grant` means the owner must reconnect (seven-day test-mode expiry) |
| `cq_broker_callback_rejections_total{provider}` | Twilio callbacks with a missing, malformed or bad signature (every one; the audit log aggregates them per minute) |

## Configuration

These are in addition to the shared `CQ_*` settings the broker also reads: `CQ_DATABASE_URL`, `CQ_CAPABILITY_SIGNING_KEY`, `CQ_INTERNAL_SERVICE_TOKEN`, `CQ_SECRET_KEY`, `CQ_PROFILE`, and `CQ_MODEL_GATEWAY_URL` (where LLM calls are forwarded).

| Variable | Type | Default | Secret | Profiles | Purpose |
| --- | --- | --- | --- | --- | --- |
| `CQ_MASTER_KEY_FILE` | path | none (dev key) | **yes** (the file) | demo-cpu, dgx | Master keyring, mode 0600, mounted read-only into the broker and the model gateway only |
| `CQ_PROVIDER_MODE` | `fake` \| `live` | `fake` | no | all | `fake` serves Google and Twilio from built-in fakes |
| `CQ_PUBLIC_BASE_URL` | URL | `http://localhost:8080` | no | all | Public origin for the OAuth redirect and Twilio callbacks; must match exactly |
| `CQ_CONTROL_API_URL` | URL | `http://control-api:8080` | no | all | Control API internal routes |
| `CQ_KNOWLEDGE_URL` | URL | `http://knowledge:8000` | no | all | Knowledge service internal routes |
| `CQ_GOOGLE_CLIENT_ID` | string | empty | no | demo-cpu, dgx | OAuth web client ID |
| `CQ_GOOGLE_CLIENT_SECRET` | secret string | empty | **yes** | demo-cpu, dgx | OAuth web client secret |
| `CQ_TWILIO_ALLOWED_NUMBERS` | JSON list of E.164 | `[]` | no (personal data) | demo-cpu, dgx | In `live` mode, the only numbers that may be called |
| `CQ_BROKER_HOST` / `CQ_BROKER_PORT` | string / int | `0.0.0.0` / `8000` | no | all | Listen address (container network) |

## Fakes (`CQ_PROVIDER_MODE=fake`)

The fakes support fake end-to-end runs and tests. Fixtures contain no real personal data: `example.com` addresses and `+1555555xxxx` numbers.

- **Google sign-in:** call the callback with `code=fake-code` (both scopes) or `code=fake-code:gmail.readonly`.
- **Gmail fixtures:** eight messages dated yesterday: plain, multipart, HTML-only, empty, attachment-only, prompt injection, promotion, and malformed base64. `after:`/`before:` and `labelIds` are honoured, and results paginate with `resultSizeEstimate`.
- **Sheets:** stored in memory.
- **Twilio:** the destination's last digit selects the outcome.

| Last digit | Final `state` | `answered` / `speechCaptured` |
| --- | --- | --- |
| 2 | `busy` | false / false |
| 3 | `no-answer` | false / false |
| 4 | `failed` | false / false |
| 5 | `completed` | true / false |
| anything else | `completed` | true / true, transcript "Yes, I can attend." |

## Tests

Run with `pytest services/capability_broker/tests packages/secret_store/tests`. They need the PostgreSQL test server described in the root `conftest.py`. Coverage:

- every contract operation is served, and Person 5's SDK clients work against the broker;
- negative authorization for every capability operation, and `RUN_CANCELLED` after a cancel;
- only the approved script, disclosure, spreadsheet and knowledge base are accepted;
- handshake, event batches, and LLM forwarding including stream translation;
- token revocation;
- OAuth state replay, expiry, and binding, plus PKCE;
- incremental and partial grants, and seven-day expiry handling;
- Sheets formula safety;
- Twilio signature checks: the reference vector, forgery, and wrong URL; rejection audit aggregation, no decrypt for unsigned requests, and oversized bodies refused before reading;
- the call cap under concurrent requests; plain-name rules; callbacks that arrive before (or after a lost) create response;
- Sheets range scoping to `inputRange` and `resultRange`;
- single-flight Google refresh without a held database connection; disconnecting with an undecryptable secret; unusable OAuth tokens;
- duplicate and late callbacks;
- in-doubt calls that are never redialed;
- secrets and full numbers absent from the database, logs, and API;
- a lifecycle test against the real control API.
