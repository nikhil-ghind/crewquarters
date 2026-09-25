# Edge proxy, routing and callback exposure

The edge proxy is the only published HTTP entry point (PLAN.md sections 4.1, 4.2 and 14.3). It runs nginx as uid 101 with a read-only root filesystem and all capabilities dropped. Image: `infra/docker/proxy.Dockerfile`. Configuration: `infra/proxy/`. It serves the web UI and routes the API. The control API, capability broker, knowledge service, model gateway and PostgreSQL publish no ports on the appliance.

| Site | Port | Published | Who uses it |
| --- | --- | --- | --- |
| Main | 8080 in the container; host `CQ_BIND_ADDRESS:CQ_HTTP_PORT` (default `127.0.0.1:8080`) | yes | The browser, the desktop launcher (`/setup`), Google's OAuth redirect on `localhost` |
| Callbacks only | 8081 | no; reachable only on the `callbacks` Compose network | The `tunnel` service (profile `callbacks`) |
| LAN HTTPS (opt-in) | 8443 in the container; host `CQ_BIND_ADDRESS:443` | only in LAN HTTPS mode | Browsers on the LAN. In this mode the main site's HTTP port redirects to HTTPS. See [lan-https.md](lan-https.md) |

Configuration files:

- `nginx.conf`: the default mode.
- `nginx-lan-https.conf`: LAN HTTPS mode.
- Both include the same pieces:
  - `common.conf`: http-level settings;
  - `main-site.conf`: the routes below;
  - `callbacks-site.conf`;
  - the header and error snippets.
- `tls.conf` and `ca-download.conf`: used in LAN HTTPS mode only.

## Routing (main site, port 8080)

nginx picks the most specific match. The upstream is resolved at request time through Docker DNS, so the proxy starts, and serves the UI, even while a backend is restarting.

| Path | Goes to | Body limit | Notes |
| --- | --- | --- | --- |
| `/internal`, `/internal/*` | **404** (JSON envelope) | | Service-to-service routes never cross the edge. Encoded or dot-segment variants (`//internal`, `/api/v1/../../internal`, `%2e%2e`, `..%2f`) are normalized first and also get 404 |
| `/api/v1/connections/google/callback` (exact) | capability broker `:8000` | 64 KiB | Google OAuth redirect. Rate limited per client IP: 20 r/s, burst 40, then `429 RATE_LIMITED` |
| `/api/v1/callbacks/twilio/*` | capability broker `:8000` | 64 KiB | Twilio voice, gather and status callbacks. Signature-checked by the broker. Same rate limit |
| `/api/v1/knowledge-bases/{id}/documents` | control API `:8080` | 26 MiB | Document upload: `CQ_MAX_UPLOAD_BYTES` (25 MiB) plus multipart overhead. Streamed to the control API without buffering; the control API enforces the exact limit |
| `/api/*`, including every other `/api/v1/connections/*` path | control API `:8080` | 2 MiB | Includes Google start, test and delete, and the Twilio PUT, test and test-call routes. Responses are not buffered, so SSE streams (run events, chat, model state) pass straight through |
| `/assets/*` | static UI files | | `Cache-Control: public, max-age=31536000, immutable`. A missing file is a 404, not the SPA |
| `/index.html` | static UI | | `Cache-Control: no-cache` |
| anything else (for example `/setup`, `/runs/123`) | static file if it exists, else `index.html` (SPA fallback) | | `Cache-Control: no-cache` |

## Headers

- **Upstream request headers.** The proxy is the edge, so it replaces the forwarding headers instead of appending to them: `X-Forwarded-For` and `X-Real-IP` are set to the client address, `X-Forwarded-Proto` to the scheme, `X-Forwarded-Host` to the `Host` header, and `X-Forwarded-Port` to the port. `Forwarded` is removed. `X-Request-Id` keeps the caller's value when it matches `[A-Za-z0-9._:-]{8,128}` and is otherwise generated. Upstream services echo it.
- **Every response.** `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`, and a restrictive `Permissions-Policy`. The proxy drops upstream copies of these headers, so they never appear twice.
- **UI responses.** A same-origin-only CSP: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; ...; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'`. The UI build must not use inline scripts or inline `<style>`, and must not load from other hosts. React `style={...}` props are set through the DOM, so they are allowed. To relax the policy, edit `infra/proxy/ui-headers.conf`.
- **Errors the proxy produces** use the platform envelope `{"error": {code, message, requestId, details}}`:
  - `404 NOT_FOUND`;
  - `413 PAYLOAD_TOO_LARGE`;
  - `429 RATE_LIMITED`, with `Retry-After`;
  - `503 SERVICE_UNAVAILABLE`, when a backend is down or starting.
- **HSTS.** `Strict-Transport-Security: max-age=31536000` is sent only over HTTPS to a host name, which means only in LAN HTTPS mode, and never to `localhost` or an IP address.

  Upstream errors pass through unchanged.
- **Access log.** JSON on stdout: method, path **without the query string**, status, bytes, duration and request ID. It never logs the referrer, because OAuth codes, tokens and phone numbers travel in query strings and referrers (PLAN.md section 10.2).

## Web UI

The proxy image builds the web UI (`apps/web`, `npm ci` and `npm run build` in a Node stage) and serves the result, so every image carries the UI that matches its source tree. `make image` or `docker compose ... up --build` is enough; no separate UI build step is needed. The UI font (Inter 4.1, SIL OFL; `apps/web/src/assets/fonts/inter/`) is bundled and served from `/assets/`, so the `font-src 'self'` CSP holds.

The desktop launcher waits for `/api/v1/health/ready`. It then opens `/setup` the first time and `/` afterwards, at the address in `/var/lib/crewquarters/public/ui-url`: `http://localhost:8080`, or `https://NAME.local` in LAN HTTPS mode.

## Callback exposure (profile `callbacks`)

Google and Twilio use separate callback origins (ADR 0009, "Revision"):

| Provider | Origin | Setting | Why |
| --- | --- | --- | --- |
| Google OAuth redirect | The origin the owner's **browser** uses | `CQ_PUBLIC_BASE_URL` (default `http://localhost:8080`) | Google redirects the browser. The OAuth binding cookie lives on that origin |
| Twilio voice, gather and status | The public **tunnel** origin | `CQ_TWILIO_CALLBACK_BASE_URL` (empty means `CQ_PUBLIC_BASE_URL`) | Twilio calls from the internet and signs the exact URL. The broker validates signatures against this origin |

Google OAuth works without any tunnel: Google accepts `http://localhost` redirect URIs, so the redirect reaches the broker through the main site on the owner's own machine. Twilio needs a public HTTPS URL, and that is what the tunnel is for (PLAN.md section 14.3). Use it only for controlled demos, and stop it afterwards. `GET /api/v1/settings` returns both values as `callbackUrls.googleRedirectUri` and `callbackUrls.twilioCallbackBase`. The Connections page shows them.

1. Create a Cloudflare tunnel with a fixed hostname, and point that hostname's service at `http://proxy:8081`. Put its token in `/etc/crewquarters/secrets.env` as `CQ_TUNNEL_TOKEN=...`. On a laptop, export it instead.
2. Set `CQ_TWILIO_CALLBACK_BASE_URL=https://<hostname>` in `crewquarters.env`. Leave `CQ_PUBLIC_BASE_URL` alone. The broker gives Twilio callback URLs on this origin and validates Twilio signatures against it. Then restart: `sudo crewquarters restart`. On a laptop, export the variable before `docker compose up`; `infra/compose/compose.yaml` must pass it to the broker and the control API.
3. Start the tunnel: `sudo crewquarters tunnel up`. On a laptop: `docker compose -f infra/compose/compose.yaml --profile callbacks up -d tunnel`.
4. Stop it after the demo: `sudo crewquarters tunnel down`. `crewquarters down` also stops it.

The tunnel container can reach only the proxy's callbacks site, because it shares only the `callbacks` network with the proxy. The callbacks site serves exactly the two broker callback groups and returns 404 for everything else, including the UI, `/api/v1/*` and `/internal/*`. Nothing else becomes reachable from the internet.

> **Do not point `CQ_PUBLIC_BASE_URL` at the tunnel.** That moves the Google redirect to the tunnel hostname. The OAuth binding cookie is set on the origin the browser uses, so it is not sent to the tunnel hostname, and Google sign-in fails the browser-binding check. Use `CQ_TWILIO_CALLBACK_BASE_URL` for the tunnel instead. Older setups that set `CQ_PUBLIC_BASE_URL` to the tunnel keep working for Twilio, because Twilio falls back to it.

## Verifying

```bash
B=http://127.0.0.1:8080
curl -s -o /dev/null -w '%{http_code}\n' $B/internal/v1/metrics           # 404
curl -s -o /dev/null -w '%{http_code}\n' "$B/api/v1/connections/google/callback?state=x"  # 303 from the broker
curl -s $B/api/v1/health/ready                                             # control API
curl -s -o /dev/null -w '%{http_code}\n' $B/setup                          # 200 (index.html)
docker compose -p crewquarters exec proxy wget -qO- http://127.0.0.1:8081/ # 404 on the callbacks site
```

The CI package job builds the image, runs `nginx -t`, and checks that the proxy is the only service publishing a port.

`infra/proxy/test-proxy.sh <image>` runs both nginx configurations against stub backends. It checks:

- routing;
- the TLS handshake against a generated device CA;
- TLS versions and ciphers;
- HSTS;
- the HTTP-to-HTTPS redirect;
- the CA download;
- the bundled font;
- both healthchecks.
