# ADR 0007: Trust model for v1

- Status: Accepted
- Date: 2026-09-24
- Owner: Nikhil Hiro Ghind (Person 1), integration lead; reviewed by every service owner

## Context

Crewquarters runs agent code, holds provider credentials, and exposes a web UI on a single owner-operated appliance (PLAN.md sections 4.1, 10, and 16). Each service needs one shared statement of what it may trust, so no service quietly widens a boundary.

## Decision

**Zones** (PLAN.md section 4.1):

| Zone | Members | Trusted by |
| --- | --- | --- |
| Public | Browser, temporary OAuth/Twilio callbacks | Nothing: every request is authenticated or signature-checked |
| Edge | Reverse proxy | Routes only `/api/v1/*`, the UI, and the exact callback paths; never `/internal/*` |
| Control | Control API, scheduler, capability broker, knowledge, model gateway, PostgreSQL | Each other, over the private network, with the internal service token |
| Host | Runtime daemon, Docker | Control zone via the daemon's Unix socket only |
| Run | Agent and vLLM containers | Nothing. An agent reaches only the broker, with a capability token |

**Rules:**

1. **Agents are untrusted.** Curated images are pinned by digest, but an agent is still treated as hostile. It gets no secrets, no Docker, no database and no general egress. Every capability is checked outside the model and outside the agent: in the broker and gateway, against the token, and in the control API, against run state and attempt.
2. **Content is untrusted.** Email, documents, input-request schemas and event payloads are data. Schemas from agents may not contain regular expressions. Payloads are size-capped and redacted before storage or logging.
3. **Browser requests prove intent.** They carry a session cookie, an allowed Origin, and the `X-CSRF-Token` header. `Idempotency-Key` makes retries safe.
4. **Service calls prove identity.** `/internal/v1` requires the internal service token. The broker additionally verifies the agent's capability token (HS256, `aud=crewquarters-broker`). It rejects any token whose `jti` is not the current attempt's `capabilityTokenId`, or whose run is no longer active.
5. **Secrets stay where they are used.** The broker decrypts only Google and Twilio secrets. The model gateway decrypts only OpenAI and Anthropic keys. The control API never decrypts provider secrets.
6. **Least privilege, then approval.** A run's permissions are the manifest's request, intersected with the owner's approval and with the connections available at call time. A permission change requires re-approval before the next run.

## Consequences

- The runtime daemon is the highest-value target. It accepts only validated specs, never raw `docker run` arguments (PLAN.md section 5.2).
- Container isolation is not a sandbox for hostile public code. Opening the marketplace needs the section 26 hardening milestone.
- Host root can read every secret. This is accepted for a single-owner appliance.
