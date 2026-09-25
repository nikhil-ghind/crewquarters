# ADR 0010: nginx edge proxy as the only published entry point

- Status: Accepted
- Date: 2026-09-25

## Context

The control API used to publish its port directly. That had three consequences:

- `/internal/v1/*` was reachable from the edge.
- OAuth and Twilio callbacks could not reach the capability broker.
- There was nowhere to serve the web UI or to apply edge body and rate limits.

PLAN.md sections 4.1, 4.2 and 14.3 require:

- a reverse proxy that runs as non-root;
- exactly two callback groups routed to the broker;
- a 404 for internal routes;
- body and rate limits at the edge;
- an optional tunnel that exposes only the callbacks.

## Decision

- **Proxy.** One nginx container (`nginxinc/nginx-unprivileged`, digest-pinned, uid 101, read-only root filesystem, all capabilities dropped) is the only published HTTP port in both the laptop and appliance stacks. The routing table is in docs/runbooks/proxy.md.
- **Why nginx over Caddy.** nginx has per-location body limits and per-IP request rate limiting built in, and needs no plugins. Upstreams are resolved per request through Docker DNS, so the proxy starts before its backends.
- **UI.** The web UI is built in a Node stage of the proxy image and its static output is served by nginx. The UI is versioned with the proxy image and needs no host bind mount.
- **Callbacks site.** A second server block on port 8081 serves only the callback paths. The `callbacks` Compose profile's Cloudflare tunnel (digest-pinned) shares a network with the proxy and nothing else.
- **Master key.** The key is a bind mount (appliance) or a named volume (laptop), read-only, mounted into the broker and the gateway only. They read it through a supplementary group, because the containers run as uid 10001 and the file is `root:crewquarters 0640`.

## Consequences

- **Releases.** A release has two images: `crewquarters/platform` and `crewquarters/proxy`. The offline bundle carries both.
- **Client IP.** The control API sees the proxy's address as the client. It must trust `X-Forwarded-For` from the proxy, which is currently limited to `127.0.0.1` in `crewquarters_api/main.py`, or per-IP login rate limits apply to the proxy as a whole.
- **One public base URL.** Moving `CQ_PUBLIC_BASE_URL` to a tunnel hostname also moves the Google redirect (docs/runbooks/proxy.md, "Caveat").
- **UI constraint.** A strict same-origin CSP is enforced on the UI. The UI must not use inline scripts or inline styles.
