# Contracts

Versioned interfaces between the services. A contract change merges before any implementation that depends on it, in a focused PR reviewed by every affected owner (PLAN.md section 20). See `docs/adr/0004-http-contract-and-drift-checks.md`.

| File | What it is | Owner | Edited by |
| --- | --- | --- | --- |
| `openapi.yaml` | HTTP contract for `/api/v1` and `/internal/v1` of the control API | Nikhil Hiro Ghind (Person 1) | **Generated** by `make contracts` from `services/control_api` |
| `clients/typescript/schema.d.ts` | TypeScript types for the web UI | Nikhil Hiro Ghind (Person 1); consumed by Srija Taduri (Person 4) | **Generated** (openapi-typescript) |
| `clients/python/` | Python client for services, tests, and tooling | Nikhil Hiro Ghind (Person 1) | **Generated** |
| `agent-manifest.schema.json` | JSON Schema (2020-12) for `crewquarters/v1alpha1` agent manifests | Nikhil Hiro Ghind (Person 1) | Hand-written |
| `capabilities.yaml` | Capability vocabulary derived from manifest permissions and carried in run tokens | Nikhil Sajan Khaneja (Person 3) | Hand-written |
| `events/run-event.schema.json` | Run event envelope and payloads delivered over SSE | Nikhil Hiro Ghind (Person 1) | Hand-written |

## Changing a contract

- **HTTP:** change the Pydantic models or routes in `services/control_api`, run `make contracts`, and commit the regenerated `openapi.yaml` and `clients/`. Never edit generated files by hand. CI regenerates them and fails on any diff.
- **Manifest schema, capabilities, or events:** edit the JSON/YAML, add or adjust tests and UI copy, and request review from the affected owners:
  - Vineet Kumar (Person 5) for SDK and agents;
  - Nikhil Sajan Khaneja (Person 3) for capabilities;
  - Srija Taduri (Person 4) for anything the UI renders.
- **Compatibility:** additive changes are preferred. Removing or renaming a field needs a new `apiVersion` or a coordinated change across every consumer.
