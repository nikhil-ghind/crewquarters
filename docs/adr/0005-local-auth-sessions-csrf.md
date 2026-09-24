# 0005. Local owner authentication, sessions, and CSRF

- Status: Accepted
- Date: 2026-09-24
- Owner: Nikhil Hiro Ghind (Person 1)

## Context

The appliance has one owner and binds to localhost by default. LAN mode serves HTTPS (PLAN.md section 10.1). The browser UI and the API share an origin. OIDC, SAML, and passkeys are deferred.

## Decision

**Bootstrap**

- `cq-admin bootstrap-token` (run by the installer) prints a one-time setup code.
- Only the code's SHA-256 hash and its expiry (default 24 h) are stored, in the `settings` row `bootstrap.token`.
- `POST /api/v1/bootstrap` compares hashes in constant time, creates the owner, deletes the setting, and starts a session.
- Bootstrap works once: after a user exists it returns `409 ALREADY_BOOTSTRAPPED`.

**Passwords**

- Argon2id with argon2-cffi defaults (`crewquarters_api/security.py`), minimum 12 characters.
- A missing user still costs one Argon2 verification, which equalizes timing.
- Hashes are upgraded on login when the parameters change.

**Sessions**

- The session token is an opaque 256-bit random value (`secrets.token_urlsafe(32)`). Only its SHA-256 hash is stored in `sessions`.
- Idle timeout is 12 h, sliding (refreshed at most once a minute). The absolute limit is 7 days.
- Logout sets `revoked_at`.

**Cookies**

- `cq_session`: `HttpOnly`, `SameSite=Lax`, `Path=/`, and `Secure` when `CQ_COOKIE_SECURE=true` (LAN/HTTPS mode).
- `cq_csrf`: the same attributes but readable by JavaScript.

**CSRF**

- The CSRF token is stateless: `base64url(HMAC-SHA256(CQ_SECRET_KEY, "csrf:" + session_hash))`.
- It is returned by `POST /sessions`, `POST /bootstrap`, and `GET /me`, and must be sent as `X-CSRF-Token` on every unsafe method.
- `Origin` (or `Referer` as a fallback) must be in `CQ_PUBLIC_ORIGINS` for every unsafe method, including unauthenticated bootstrap and login.

**Rate limiting and audit**

- Bootstrap and login use an in-process sliding-window limiter per client IP (`CQ_AUTH_RATE_LIMIT_PER_MINUTE`), returning `429` with `Retry-After`. This is sufficient because the appliance runs one control-API process. Multiple replicas would need a shared store.
- Successful and denied bootstrap and login attempts, and logout, write audit events. Metadata is redacted.

**Service routes**

- `/internal/v1` routes require `Authorization: Bearer <CQ_INTERNAL_SERVICE_TOKEN>`, compared in constant time.
- The reverse proxy never routes `/internal/`. Only the capability broker, model gateway, and runtime components call these routes, over the private network.

## Consequences

- Cross-site requests fail twice: once on Origin and once on CSRF.
- Stolen database rows do not reveal session tokens or the bootstrap code.
- Rotating `CQ_SECRET_KEY` invalidates CSRF tokens; users re-fetch them from `/me`.
- Password recovery is out of scope for v1. The setup wizard warns the owner.
