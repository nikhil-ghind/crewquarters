# ADR 0011: Google OAuth and inbound callbacks

- Status: Accepted
- Date: 2026-09-25
- Owner: Nikhil Sajan Khaneja (Person 3)

## Context

Two demo agents need Google: the Gmail digest reads mail, and the caller reads and writes a spreadsheet (PLAN.md sections 10.3 and 12). The owner grants that access once, and agents use it later with no browser present, so the device needs a long-lived refresh token.

Constraints:

- Google redirects the browser to an exact, pre-registered URI. On a headless GB10 that URI must be reachable from the owner's laptop.
- Twilio also needs to reach the device from the internet (PLAN.md section 14.3).
- Agents must never hold provider credentials (PLAN.md sections 1 and 10.2).
- The app stays in Google "testing" mode for the demo. Refresh tokens for these scopes expire after seven days, and the test-user list is capped.

## Decision

**Flow.** The capability broker runs the OAuth web-server authorization-code flow in `services/capability_broker/src/crewquarters_broker/google.py` (`GoogleConnector`).

- `start()` builds the authorize URL with `access_type=offline`, `prompt=consent`, `include_granted_scopes=true`, and PKCE (`code_challenge_method=S256`, with a 64-byte `secrets.token_urlsafe` verifier).
- Only two scopes can ever be requested: `gmail.readonly` and `spreadsheets` (`SCOPES`). Anything else is `INVALID_SCOPE`.
- The owner consents to Gmail and Sheets as separate actions. `include_granted_scopes` makes the grants accumulate on one connection.
- One Google account per device. A successful callback deletes any previous Google connection and its secret.

**State.** `state` is 32 random bytes (`secrets.token_urlsafe(32)`), single use, and valid for `STATE_TTL_SECONDS = 600`. Only its SHA-256 hash is kept, in the broker's memory (`_pending`), next to the PKCE verifier and the hash of the browser binding. The callback pops the entry before checking anything else, so a state is consumed even when the callback fails.

**Browser binding.** `start()` also returns a second random value, `browserBinding`. The control API never returns it in a response body. It sets it as a cookie (`services/control_api/src/crewquarters_api/routers/connections.py`):

- name `cq_oauth_binding`, `HttpOnly`, `SameSite=Lax`, `Max-Age=600`;
- `Path=/api/v1/connections/google`;
- `Secure` when `CQ_COOKIE_SECURE` is set or the request is HTTPS.

The callback compares the hashes with `hmac.compare_digest`. A missing or wrong cookie returns `OAUTH_STATE_INVALID` ("Start Google sign-in from this browser"). This stops login CSRF: an attacker cannot make the owner's browser finish the attacker's authorization.

**Callback.** `GET /api/v1/connections/google/callback` (`crewquarters_broker/callbacks.py`):

1. It exchanges the code with the verifier.
2. It requires a refresh token and at least one known granted scope, or it returns `OAUTH_SCOPE_MISSING`.
3. It reads the account label from the Gmail profile, only when Gmail was granted.
4. It redirects (303) to `{CQ_PUBLIC_BASE_URL}/connections/google?result=connected`, or `?result=error&code=...`, and deletes the cookie. Tokens never appear in the redirect.

**Token storage.**

- The refresh token is stored through `crewquarters_secret_store.db.store(provider="google", owner_type="oauth_connection")`. That is AES-256-GCM envelope encryption under the device master key (`CQ_MASTER_KEY_FILE`).
- Access tokens stay only in broker memory (`_access`) and are refreshed 60 s before expiry (`EXPIRY_MARGIN_SECONDS`).
- Refreshes are single-flight per connection (`_refresh_locks`), and no database connection is held while Google answers.
- `invalid_grant` on refresh, which is the normal seven-day testing-mode expiry, sets the connection to `NEEDS_ATTENTION` and writes the audit event `connection.google.expired`. Agents then get `NEEDS_CONNECTION`, and the owner reconnects.
- Disconnecting revokes the token at Google (best effort) and deletes the connection and its secret.

**Redirect URI.** The redirect URI is `BrokerSettings.google_redirect_uri`, which is `CQ_PUBLIC_BASE_URL` + `/api/v1/connections/google/callback`. `CQ_PUBLIC_BASE_URL` is the only source for it (ADR 0009). The client ID and secret come from `CQ_GOOGLE_CLIENT_ID` and `CQ_GOOGLE_CLIENT_SECRET`. With `CQ_PROVIDER_MODE=fake` (the default), Google is served by `crewquarters_broker/fakes.py`.

**Callback exposure (ADR 0010).** The nginx proxy (`infra/proxy/nginx.conf`) forwards exactly two groups to the broker: `= /api/v1/connections/google/callback` and `^~ /api/v1/callbacks/twilio/`. Both are limited to 64 KB bodies and 20 requests/s per IP with a burst of 40.

- The main site, port 8080, serves them next to the UI and the control API.
- A second site, port 8081, serves only those paths and returns 404 for everything else.
- The `callbacks` Compose profile adds a digest-pinned `cloudflare/cloudflared` tunnel. It is configured with `CQ_TUNNEL_TOKEN` and shares only the `callbacks` network with the proxy. The tunnel's hostname points at `http://proxy:8081`.
- Access logs omit query strings, so OAuth codes are not logged.

## Alternatives considered

- **Service-account domain-wide delegation.** It needs a Workspace domain and admin rights, and it does not fit a personal Gmail account (PLAN.md section 1).
- **An OAuth library.** PLAN.md section 10.3 says "with a library". The code uses plain `httpx` instead: the flow is three requests, and authlib, the one candidate, deprecates its httpx integration in 1.8 (docs/capability-broker.md). *Divergence from PLAN.md.*
- **Storing state in PostgreSQL.** It would survive a broker restart. Sign-in takes seconds and a retry is cheap, so state stays in memory.
- **The out-of-band or loopback flow.** Google has removed out-of-band, and a loopback redirect cannot serve a headless device reached from another computer.
- **Exposing the whole UI through the tunnel.** It would put login and every API route on the internet. Only the two callback groups are exposed.

## Consequences

- If the broker restarts during sign-in, the pending state is lost and the owner clicks Connect again. The same applies with more than one broker replica.
- The binding cookie is set on the origin the owner uses for the UI. If `CQ_PUBLIC_BASE_URL` points at the tunnel hostname while the UI is opened on `localhost`, the cookie is not sent and sign-in fails the binding check. Connect Google before switching to the tunnel, or open the UI through the tunnel hostname (docs/runbooks/proxy.md, "Caveat"). A per-provider base URL would fix this; the broker owns that change.
- In testing mode, the owner must reconnect every seven days. `cq_broker_oauth_refresh_failures_total{reason="invalid_grant"}` counts these expiries.
- `prompt=consent` shows the consent screen on every connect. That guarantees a refresh token is returned.
- A restored backup cannot use Google without the master key that encrypted the refresh token.
- Twilio callbacks use the same exposure path and check `X-Twilio-Signature` against URLs built from `CQ_PUBLIC_BASE_URL`.
