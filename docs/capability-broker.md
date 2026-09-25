# Capability broker

Owner: Nikhil Sajan Khaneja (Person 3). Code: `services/capability_broker` (`crewquarters_broker`) and `packages/secret_store` (`crewquarters_secret_store`).

The broker is the only service that handles Google and Twilio credentials. An agent container can reach only the broker. It gets narrow operations authorized by its run capability token. It never gets secrets, and it never gets a generic proxy.

## Surfaces

| Prefix | Caller | Authentication |
| --- | --- | --- |
| `/agent/v1/*` | Agent containers (the SDK) | `Authorization: Bearer $PLATFORM_RUN_TOKEN` |
| `/internal/v1/*` | Control API (connections), on the owner's behalf | `Authorization: Bearer $CQ_INTERNAL_SERVICE_TOKEN` |
| `/api/v1/connections/google/callback` and `/api/v1/callbacks/twilio/*` | Google and Twilio, through the reverse proxy | OAuth `state` plus browser binding; Twilio signature |

The reverse proxy must forward only the two public callback groups to the broker (PLAN.md sections 4.1 and 14.3).

## Capability checks

Every `/agent/v1` call passes all of these checks (`crewquarters_broker/auth.py`):

1. **Token:** a valid HS256 signature, `aud=crewquarters-broker`, issuer, and expiry.
2. **Run state:** the control API's run view (`GET /internal/v1/runs/{id}`) shows an active state. The token's `jti` must equal `capabilityTokenId`, and its attempt and installation must match. A token from an earlier attempt is therefore revoked.
3. **Capability:** the capability is in the token **and** in the run's current approved permissions. This is an intersection, so narrowing an approval takes effect immediately.
4. **Resource:** the knowledge base and spreadsheet IDs come from the approved installation config (see `resource` in `packages/contracts/capabilities.yaml`), never from the agent's request.
5. **Connection:** the provider connection exists and has the needed scope. Otherwise the call returns `409 NEEDS_CONNECTION`.
6. **Cancellation:** side effects (calls and sheet writes) stop once cancellation is requested (`409 CANCELLED`).

## Agent API (`/agent/v1`): contract for the SDK

The run and attempt always come from the token. JSON is camelCase. Errors use the platform shape `{"error": {code, message, requestId, details}}`. The codes map to the SDK's typed exceptions:

| Code | Status | SDK exception |
| --- | --- | --- |
| `UNAUTHENTICATED` | 401 | stop: the token is invalid, revoked, or for an old attempt |
| `RUN_NOT_ACTIVE` | 409 | stop |
| `PERMISSION_DENIED` | 403 | `PermissionDenied` |
| `NEEDS_CONNECTION`, `NEEDS_CONFIGURATION` | 409 | `NeedsConnection` |
| `CANCELLED` | 409 | `Cancelled` |
| `RATE_LIMITED` | 429 | `RateLimited` |
| `INVALID_INPUT` | 422 | `InvalidInput` |
| `PROVIDER_ERROR`, `PROVIDER_UNAVAILABLE` | 502, 503 | retry only if the operation is idempotent |
| `PROVIDER_IN_DOUBT` | 503 | never retry blindly; check the call first |

### Run lifecycle

These are forwarded to the control API with the token's attempt.

| Route | Capability | Body |
| --- | --- | --- |
| `POST /agent/v1/handshake` | — | — |
| `POST /agent/v1/heartbeat` | — | — (returns `cancelRequested`) |
| `POST /agent/v1/events` | `events.write` | `{type: run.log\|run.progress\|run.metric\|run.artifact, payload}` |
| `POST /agent/v1/result` | — | `{status: succeeded\|failed, result?, error?}` |
| `POST /agent/v1/input-requests` | `user_input` | `{key, title, prompt, schema, timeoutSeconds, preview?}` |
| `GET /agent/v1/input-requests/{id}?wait=0..30` | `user_input` | long-poll; only this run's requests |
| `POST /agent/v1/actions/{key}/claim` | `idempotency` | — → `claimed \| completed \| in_doubt` |
| `POST /agent/v1/actions/{key}/complete` | `idempotency` | `{result}` |

### Knowledge

`POST /agent/v1/knowledge/search` needs `knowledge.search:config`. The body is `{query, topK?, maxContextTokens?, filters?: {documentIds}}`. The knowledge base is `config.knowledgeBaseId`. The response is the knowledge service's query result, described in [knowledge.md](knowledge.md).

### Gmail (`google.gmail.readonly`)

- `GET /agent/v1/google/gmail/messages?q=&pageToken=&maxResults=1..500` returns `{messages: [{id, threadId}], nextPageToken}`. `q` uses Gmail search syntax, for example `after:1718841600 before:1718928000`.
- `GET /agent/v1/google/gmail/messages/{id}?maxChars=100..100000` returns a sanitized message:
  - `{id, threadId, labelIds, internalDate, from, to, cc, subject, date, snippet, body, bodyTruncated, attachments: [{filename, mimeType, size}], link}`;
  - the body is plain text only: `text/plain` is preferred, and HTML is reduced to text with scripts and styles dropped;
  - attachment bytes are never returned;
  - email is untrusted evidence.

### Sheets (`google.spreadsheets`, spreadsheet = `config.spreadsheetId`)

- `GET /agent/v1/google/sheets/values?range=Contacts!A2:D` returns `{range, values}`.
- `POST /agent/v1/google/sheets/values:append` takes `{range, values}` and returns `{updatedRange, updatedRows}`.
- `PUT /agent/v1/google/sheets/values` takes `{range, values}` and overwrites the range.

Values are written with `valueInputOption=RAW`, so a formula from untrusted text (`=IMPORTXML(...)`) is stored as text and never evaluated. The broker never retries an append; use `ctx.idempotency` around it.

### Telephony (`twilio.call.fixed_script`)

- `POST /agent/v1/telephony/calls` takes `{to: "+E164", idempotencyKey, variables?: {name}}` and returns a call.
  - **Script:** the script is `config.script`, and only `{name}` is substituted. The name is limited to 80 characters, with `<>{}` and control characters removed.
  - **Limits:** `config.maxCalls` (default 3, at most 10) caps calls per run. `config.responseSeconds` (default 20, range 5–60) bounds the speech gather.
  - **Idempotency:** the same key returns the same call and never redials.
- `GET /agent/v1/telephony/calls/{id}` returns `{id, idempotencyKey, to: "***1234", state, outcome, transcript, createdAt, updatedAt}`.
  - `outcome` is one of `pending`, `answered_speech`, `answered_no_speech`, `busy`, `no_answer`, `failed`, `canceled`, `in_doubt`.
  - Only this run's calls are visible.

The TwiML is fixed:
1. It first discloses that this is an automated demo call from Crewquarters.
2. It then plays the approved script inside a bounded `<Gather input="speech">`.

The first transcript wins, and late or duplicate status callbacks never move a call backwards. **Nothing here makes a call legally compliant.** Restrict live tests to consenting, verified team numbers (`CQ_TWILIO_ALLOWED_NUMBERS`).

If Twilio's answer to a create request is lost, the call becomes `in_doubt` and is never redialed automatically. A rejected request becomes `failed`, and no call was placed.

## Internal API for the control API (`/internal/v1`)

| Route | Purpose |
| --- | --- |
| `GET /connections` | Status for google, twilio, openai, anthropic in the `ConnectionStatusClient` shape: `{provider, displayName, status: NOT_CONNECTED\|CONNECTED\|NEEDS_ATTENTION\|DISABLED, grantedCapabilities, lastCheckedAt, ...}` |
| `POST /connections/google/start` | `{userId, capabilities: [gmail.readonly, spreadsheets]}` → `{authorizationUrl, browserBinding}` |
| `POST /connections/google/test` | Refresh now; an expired grant becomes `NEEDS_ATTENTION` |
| `DELETE /connections/google?userId=` | Revoke at Google, then delete the connection and secret |
| `PUT /connections/twilio` | `{userId, accountSid, authToken, fromNumber}`: save (or replace), then validate without calling |
| `POST /connections/twilio/test` / `DELETE /connections/twilio?userId=` | Validate / delete |
| `GET/POST /provider-profiles`, `DELETE /provider-profiles/{id}?userId=` | OpenAI and Anthropic keys. The broker only encrypts them; the model gateway decrypts and tests them |

Responses never contain secret values.

**Google sign-in (browser binding).**
1. The control API calls `start`.
2. It sets `browserBinding` as a cookie: `cq_oauth_binding`, `HttpOnly`, `SameSite=Lax`, `Secure` under HTTPS, `Path=/api/v1/connections/google`, `Max-Age=600`.
3. It redirects the browser to `authorizationUrl`.

The callback requires that cookie, so an attacker cannot make the owner's browser complete the attacker's authorization. The broker then redirects the browser to `/connections/google?result=connected` or `?result=error&code=OAUTH_STATE_INVALID|OAUTH_DENIED|OAUTH_CODE_INVALID|OAUTH_SCOPE_MISSING|...`.

## Google OAuth details

- **Flow:** the web-server authorization-code flow with PKCE (S256), `access_type=offline`, `prompt=consent`, and `include_granted_scopes=true`. Gmail and Sheets are consented separately and accumulate on one connection.
- **Scopes:** only `gmail.readonly` and `spreadsheets` are ever requested. No identity scopes are requested; the account label comes from the Gmail profile when Gmail is granted.
- **Redirect URI:** exactly `CQ_PUBLIC_BASE_URL` + `/api/v1/connections/google/callback`. Register this exact URI in Google Cloud.
- **State:** 256 random bits, single use, valid for 10 minutes; only its hash is kept in memory. A broker restart during sign-in means the owner has to click Connect again.
- **Tokens:** refresh tokens are envelope-encrypted, and access tokens are kept in memory only.
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
- **Development:** the `dev` profile uses a fixed, insecure development key when no file is set. Every other profile refuses to start without one.

## Configuration

These are in addition to the shared `CQ_*` settings (`CQ_DATABASE_URL`, `CQ_CAPABILITY_SIGNING_KEY`, `CQ_INTERNAL_SERVICE_TOKEN`, `CQ_SECRET_KEY`, `CQ_PROFILE`).

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
- **Gmail fixtures:** eight messages dated yesterday: plain, multipart, HTML-only, empty, attachment-only, prompt injection, promotion, and malformed base64. `after:`/`before:` are honoured, and results paginate.
- **Sheets:** stored in memory.
- **Twilio:** the destination's last digit selects the outcome.

| Last digit | Outcome |
| --- | --- |
| 2 | busy |
| 3 | no answer |
| 4 | failed |
| 5 | answered, no speech |
| anything else | answered, speech "Yes, I can attend." |

## Tests

Run with `pytest services/capability_broker/tests packages/secret_store/tests`. They need the PostgreSQL test server described in the root `conftest.py`. Coverage:

- negative authorization for every agent route;
- token revocation;
- OAuth state replay, expiry, and binding, plus PKCE;
- incremental and partial grants, and seven-day expiry handling;
- Sheets formula safety;
- Twilio signature checks: the reference vector, forgery, and wrong URL;
- duplicate and late callbacks;
- in-doubt calls that are never redialed;
- secrets and full numbers absent from the database, logs, and API;
- a lifecycle test against the real control API.
