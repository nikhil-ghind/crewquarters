# 0004. HTTP contract source and drift checks

- Status: Accepted
- Date: 2026-09-24
- Owner: Nikhil Hiro Ghind (Person 1)

## Context

PLAN.md makes OpenAPI the canonical HTTP contract and requires generated TypeScript and Python clients to be committed and checked for drift. A hand-written OpenAPI file that drifts from the implementation is worse than none.

## Decision

**Source of the HTTP contract.** The FastAPI routes and Pydantic models in `services/control_api` (`crewquarters_api/schemas.py`, `routers/`) are the source. `make contracts`:

1. renders `packages/contracts/openapi.yaml` deterministically through `crewquarters_api/openapi.py` (fixed key order, a generated-file header, security schemes added);
2. generates TypeScript types with `openapi-typescript` into `packages/contracts/clients/typescript/schema.d.ts`;
3. generates the Python client into `packages/contracts/clients/python`.

CI regenerates all three and fails on any `git diff`. The committed files are what other teams build against.

**Hand-written canonical schemas** (JSON Schema draft 2020-12):

- `agent-manifest.schema.json`: the agent manifest (`crewquarters/v1alpha1`);
- `capabilities.yaml`: the capability vocabulary, owned by Nikhil Sajan Khaneja (Person 3);
- `events/run-event.schema.json`: run events delivered over SSE.

**Change process.** A contract change is a focused PR, reviewed by every affected owner, that merges before any implementation that depends on it (PLAN.md section 20).

**Wire conventions:**

- JSON is camelCase over HTTP; Python and the database use snake_case.
- IDs are UUIDv7 (`crewquarters_shared/ids.py`). Timestamps are RFC 3339 UTC.
- Public routes live under `/api/v1`; service-to-service routes under `/internal/v1`.
- Errors use one shape: `{"error": {"code", "message", "requestId", "details"}}`. `requestId` echoes `X-Request-ID`.
- Lists are keyset-paginated: `{items, nextCursor}`.

**Idempotency** (`crewquarters_api/idempotency.py`). Mutating POSTs accept an `Idempotency-Key` header of 8–200 characters:

- The first request stores its status and body in `idempotency_records`, in the same transaction as its effect.
- A retry with the same key and the same method, path, and body replays the stored response with `Idempotent-Replayed: true`.
- Reusing a key with a different request returns `409 IDEMPOTENCY_KEY_REUSED`. A retry that arrives while the original is still running returns `409 REQUEST_IN_PROGRESS`.
- A request that fails rolls back its record, so a retry executes again.
- Requests without a key get a generated key, echoed in the response header.

## Consequences

- Behavior and documentation cannot drift silently.
- Clients are regenerated whenever the server changes. Consumers pin to the committed file.
- The Pydantic models, not a YAML editor, are where HTTP contract edits happen.
