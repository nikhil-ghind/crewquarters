# Web UI developer guide

Owner: Srija Taduri (Person 4). Code: `apps/web`. Specification: PLAN.md section 13.

The web UI is a static single-page app (React 19, TypeScript strict, Vite, TanStack Query, React Router). It talks only to its own origin under `/api/v1`, through the generated OpenAPI types. It never calls vLLM, Google, Twilio, OpenAI, Anthropic, the model gateway or the runtime daemon.

## Commands

Run these from `apps/web`. Use Node 24, or Node 22.18 or later: the mock server and scripts are TypeScript run directly by Node.

| Command | What it does |
| --- | --- |
| `npm ci` | Install the pinned dependencies from `package-lock.json` |
| `npm run dev` | Vite dev server on http://127.0.0.1:5173. It proxies `/api` to `CQ_API_TARGET` (default `http://127.0.0.1:8080`) |
| `npm run mock` | Mock control API on http://127.0.0.1:4010. Add `-- --static dist` to also serve the build |
| `npm run typecheck` / `npm run lint` | `tsc -b` in strict mode, and ESLint (type-checked rules, react-hooks, jsx-a11y, and the safe-rendering and no-provider-SDK rules) |
| `npm test` | Vitest: units, components and key flows, with MSW mocks typed against the generated client |
| `npm run build` | Type-check, then build the static bundle into `apps/web/dist/` |
| `npm run check:bundle` | Fails if `dist/` contains provider, model-server or internal hostnames, inline scripts or source maps |
| `npx playwright test` | End-to-end tests against the mock API. They build the app, serve it from the mock server, and include the axe and responsive checks |
| `npm run gen:api` | Regenerate `packages/contracts/clients/typescript/schema.d.ts` from `openapi.yaml`, with the same generator and version as `make contracts` |
| `npm run gen:api:check` | Fail if the committed client differs from a fresh generation |

### Against a real control API

1. Start the control API and the scheduler with the dev profile. Allow the Vite origin:

   ```bash
   CQ_PUBLIC_ORIGINS='["http://127.0.0.1:5173","http://localhost:5173"]'
   ```

2. Run the UI:

   ```bash
   CQ_API_TARGET=http://127.0.0.1:8080 npm run dev
   ```

3. Print a setup code with `cq-admin bootstrap-token`, then open http://127.0.0.1:5173/setup.

## Layout

| Path | Contents |
| --- | --- |
| `src/api/` | The data layer. **`client.ts`**: openapi-fetch with CSRF, Idempotency-Key, timeout, session-expiry and connectivity middleware. **`queries.ts` and `mutations.ts`**: TanStack Query hooks and cache keys. **`sse.ts` and `streams.ts`**: resilient SSE, reconnect and polling fallback. **`chat.ts`**: POST SSE for chat. **`guards.ts`**: disabled-action reasons |
| `src/components/` | Component library (section 13.15): StatusBadge, LocalityChip, Button, ResourceMeter, Progress, DataTable, Stepper, Timeline, SchemaForm, PermissionRow, ConfirmDialog/Modal, Toast/announcer, ErrorPanel, EmptyState, Skeleton, LogViewer, SourceDrawer, QueryView |
| `src/lib/` | `status.ts` (the status vocabulary), `format.ts`, `jsonSchema.ts`, `permissions.ts`, `redact.ts`, `storage.ts`, `breakpoints.ts` |
| `src/styles/tokens.css` | Semantic design tokens copied from section 13.14 |
| `src/shell/` | App shell: sidebar, top bar, global banners, auth gate |
| `src/features/` | One folder per area. Every page is lazy-loaded, one chunk per route |
| `mock/` | Mock control API used by Playwright and for UI work |
| `e2e/` | Playwright specs, page objects, and the axe and responsive checks |

## Routes (section 13.2)

- **Overview:** `/`
- **Crew:**
  - `/agents/installed`
  - `/agents/marketplace`
  - `/agents/marketplace/:agentId` and `…/install`
  - `/agents/:installationId`, with tab routes `/runs`, `/schedule`, `/configuration`, `/permissions` and `/advanced`
- **Activity:** `/activity/runs`, `/activity/approvals`, `/runs/:runId`
- **Models:** `/models`, `/models/:modelId`
- **Knowledge:** `/knowledge`, `/knowledge/:kbId`
- **Chat:** `/chat`, `/chat/:sessionId`
- **Connections:**
  - `/connections`
  - `/connections/:provider`, where `/connections/google` is also the Google OAuth return page
- **Schedules:** `/schedules`
- **System:** `/system/status`, `/system/audit`, `/system/settings`, `/system/backups`
- **Setup:** `/setup/:step` (the wizard is resumable)
- **Sign-in:** `/login`
- **Component gallery:** `/__gallery`

The component gallery uses static fixtures and makes no API calls. It shows every component in its ready, loading, empty, degraded, error and disabled states.

## Rules the code enforces

- **One data layer.** Generated types (`@contracts/clients/typescript/schema`) plus TanStack Query. Components never keep their own copy of server state.
- **Auth and CSRF.**
  - The session is the HttpOnly `cq_session` cookie.
  - Every POST, PUT, PATCH and DELETE sends `X-CSRF-Token`. The token comes from `SessionOut.csrfToken`, or from the readable `cq_csrf` cookie after a reload.
  - A 401 while signed in sends the owner to `/login?expired=1&next=…`, and back after sign-in.
- **Idempotency.**
  - Every mutation sends `Idempotency-Key`.
  - Each user intent keeps a stable key (`useIntentKey`). A double click, or a retry after an unknown outcome, therefore replays the stored response and never repeats the side effect.
  - Keys come from `crypto.getRandomValues`, because `randomUUID` needs a secure context and LAN HTTP isn't one.
- **Timeouts.** Requests abort after 30 s. A timed-out mutation is reported as `OUTCOME_UNKNOWN`, the page re-reads the resource, and nothing is retried automatically.
- **SSE** for runs and models:
  - Runs load their history first, then stream with `?after=<seq>`. The browser resends `Last-Event-ID` itself.
  - Events are de-duplicated by sequence.
  - After 3 failures the UI falls back to bounded polling of `/runs/{id}/events/history`: 6 polls at 2–10 s intervals, then SSE is tried again.
  - Reconnecting streams raise the amber "Live updates paused—reconnecting" banner.
- **Chat streaming.** Chat uses `fetch` to read the POST event stream. Stop aborts the request, and the server keeps the partial reply as `stopped`.
- **No raw HTML.**
  - Agent, provider and document text is always rendered as React text.
  - ESLint forbids `dangerouslySetInnerHTML`, `innerHTML` and `outerHTML` assignments, and `eval`.
  - Nothing is rendered as Markdown.
  - "Open in Gmail" links are followed only for `https://mail.google.com`.
  - The Google authorization URL is followed only when it is https or same-origin.
- **No provider SDKs.** ESLint blocks imports from `openai`, `@anthropic-ai/*`, `twilio` and `googleapis`. `check:bundle` greps the build for provider hosts.
- **No duplicated server logic.** Readiness, permissions matching, model admission and schedule occurrences are shown as the API reports them. Schedule presets only compose a cron string, and `/schedules/preview` computes the occurrences. The browser keeps only preferences and unsaved non-secret drafts: the sidebar state and the install-wizard form.

## Design tokens and vocabulary

- **Tokens.** `src/styles/tokens.css` holds the section 13.14 values verbatim. The pale semantic backgrounds are computed with `color-mix`, at ratios chosen so every semantic text color keeps a contrast of at least 4.5:1. The file header lists the ratios.
- **Theme.** v1 ships only the light theme, as the plan specifies. No component hard-codes a color.
- **Status labels.** Every label comes from `src/lib/status.ts`: run states from 13.7, model disk and memory states from 13.8, connection states from 13.10, device states from 13.3, checks from 13.4, and caller result states from 13.12.
- **Breakpoints.** 768 px and 1280 px, from section 13.18:
  - Below 768 px the top bar gets a navigation drawer and tables become card lists.
  - From 768 to 1279 px the sidebar is icon-only by default.
  - The owner's collapse choice is stored per browser.

## Contract coverage

Every HTTP call goes through the generated client (`src/api/client.ts`, `queries.ts`, `mutations.ts`, `endpoints.ts`), so there are no hand-written API types. Two contract fields are untyped objects in `openapi.yaml`, and `src/lib/knowledge.ts` narrows them at runtime:

- `ChatMessageOut.citations[]`, which is read as a `CitationOut` without `documentAvailable`;
- `DocumentOut.extracted` and `error`.

Two POST routes stream server-sent events, which the OpenAPI types don't describe:

- chat messages, read with `fetch` in `src/api/chat.ts`;
- the run and model event streams, read with `EventSource` in `src/api/sse.ts`.

`npm run gen:api:check` fails CI if `schema.d.ts` drifts from `openapi.yaml`.

## Serving the build (reverse proxy)

`npm run build` produces `apps/web/dist/`:

- `index.html`;
- `favicon.svg`;
- hashed files under `assets/`.

There are no inline scripts and no source maps. The UI must be served from the **same origin** as `/api/v1`. The proxy must do the following.

- **Paths:**
  - Serve `dist/` at `/`.
  - Route `/api/v1/*` to the control API. The exceptions are `/api/v1/connections/google/callback` and `/api/v1/callbacks/twilio/*`, which go to the capability broker (docs/capability-broker.md).
  - Don't send the UI route `/connections/google` to the broker. It's where the broker redirects the browser after consent.
- **SPA fallback:** any `GET` or `HEAD` that isn't under `/api/` and doesn't match a file in `dist/` returns `dist/index.html` with status 200. Missing `/assets/*` files return 404 and fall back to nothing. Don't treat a dot in the path as a file request: routes such as `/models/local.general.small` contain one and must get `index.html`.
- **Cache headers:**
  - `/assets/*`: `Cache-Control: public, max-age=31536000, immutable`
  - `index.html`, including every fallback response: `Cache-Control: no-cache`
  - `favicon.svg`: a short cache, for example `max-age=3600`
- **Security headers on HTML responses:**

  ```
  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'
  X-Content-Type-Options: nosniff
  Referrer-Policy: same-origin
  X-Frame-Options: DENY
  ```

  - The Playwright suite runs the whole UI under this CSP (`mock/csp.ts`).
  - `connect-src 'self'` covers `fetch` and `EventSource`.
  - The Google consent page is reached by a top-level navigation, so no CSP change is needed for it.
- **Server-sent events:** responses are `text/event-stream`. Don't buffer them, compress them or time them out early.
  - Nginx: `proxy_buffering off` and `proxy_read_timeout 1h`.
  - Caddy: `flush_interval -1`.
  - The streams are `GET /api/v1/runs/{id}/events`, `GET /api/v1/models/{id}/events` and `POST /api/v1/chat/sessions/{id}/messages`.
- **Upload size:** allow request bodies of at least 25 MiB + 64 KiB on `POST /api/v1/knowledge-bases/{id}/documents`. Every other route is limited to 2 MiB by the API.
- **Headers to pass unchanged:** `Host`, `Origin`, `Cookie`, `X-CSRF-Token`, `Idempotency-Key` and `Last-Event-ID`.
  - `CQ_PUBLIC_ORIGINS` must list the exact origin the browser uses, such as `https://crewquarters.local`.
  - Set `CQ_COOKIE_SECURE=true` when serving over HTTPS.
  - The API's login rate limit keys on the client address, so forward the real client IP. Without it, every browser shares the proxy's IP bucket.

## Tests and evidence

- **Vitest** (`src/**/*.test.ts(x)`) covers:
  - the formatting, vocabulary, JSON Schema, permissions and redaction libraries;
  - the client: CSRF, idempotency, the error envelope, session expiry and offline detection;
  - SSE de-duplication, reconnect and bounded polling;
  - the component library;
  - the Crew Request card, including double answers;
  - the Gmail digest and caller renderers, and escaping;
  - sign-in and the redirect after it;
  - the bundle check.
- **Playwright** (`e2e/`) runs against the mock API (`mock/server.ts`), which enforces the real cookie, CSRF, Origin and idempotency rules. The specs cover:
  - setup;
  - Gmail digest and caller: install, run, input and approval;
  - model cold start;
  - RAG chat with citations;
  - SSE reconnect and polling;
  - the expired-OAuth reconnect;
  - degraded and offline states, session expiry and reapproval;
  - axe on every main route, with no serious or critical violations;
  - keyboard checks;
  - screenshots at 1440, 1024, 768 and 390 px, in `e2e/screenshots/`.
