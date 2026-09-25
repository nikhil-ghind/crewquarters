# 0001 — Contract drafts and open questions from the Person 5 build

- Status: proposed (needs owner decisions)
- Author: Person 5 (Vineet Kumar)
- Date: 2026-09-24
- Related: `docs/superpowers/specs/2026-09-24-person5-sdk-agents-qa-design.md`, `packages/contracts/`

## Context

The Person 5 prompt in `PLAN.md` §19 says to build against *frozen* contracts. None existed. To
avoid blocking, Person 5 drafted the contracts the SDK and agents need. They are tagged
`x-status: draft` with an approving owner. A fake platform implements them, and three test layers
enforce them:

- **Contract-file validity:** `tests/contract/test_contract_files.py`.
- **Route parity:** the fake serves exactly the documented operations (`tests/contract/test_route_parity.py`).
- **Traffic conformance:** every request and response exchanged while the three agents run validates
  against the OpenAPI schemas, and every run event validates against the event schema
  (`tests/contract/test_traffic_conformance.py`).

When an owner adopts or changes a draft, update the file, re-run `make test-contract`, and adjust
the SDK if needed.

## Decisions requested

| # | Topic | Proposal (what is implemented today) | Decides |
| --- | --- | --- | --- |
| D1 | Contract drafts | Adopt or amend `agent-manifest.schema.json`, `openapi.yaml` (slice), `events/run-event.schema.json` (Person 1) and `capabilities.yaml`, `broker-sdk.openapi.yaml` (Person 3) | Persons 1, 3 |
| D2 | How configuration reaches the agent | In the handshake response (`config`), not a mounted file. It is one source of truth and needs no daemon mount (`PLAN.md` §11.3 allows an "environment pointer"). | Persons 1, 2 |
| D3 | Idempotency storage | Broker-owned, run-scoped records: `idempotency_records(run_id, key, state, result jsonb, claimed_by_attempt, completed_at)`, unique `(run_id, key)`. §6.1 has no table for `ctx.idempotency.once`. | Persons 1, 3 |
| D4 | Input-request fields | `choices` (value, label, style), `preview` (`text`/`keyValue`/`table` blocks), and `consequence`. Reusing a key with different content returns 409 `INPUT_KEY_CONFLICT`. These drive the §13.7 approval card. | Persons 1, 4 |
| D5 | Active-time clock | Whoever enforces `activeTimeoutSeconds` (the daemon, per §7.3) must learn about `WAITING_INPUT` from the broker or control API. No signal path is defined. | Persons 1, 2 |
| D6 | Caller `status` column | Blank, `ready`, or `pending` means eligible. `done`, `called`, `skip`, `dnc`, or `do-not-call` is skipped as `status`. Anything else is skipped as `unrecognized_status`. | Persons 1, 4 |
| D7 | Caller result writes | Idempotent `values:update` at `Results!A<row>:H<row>` (the source row) instead of `append`, so write retries can never duplicate. Columns: `source_row, name, phone_masked, call_sid, status, transcript, completed_at, error`. | Person 3 |
| D8 | "Rejected" call state (§12.1) | Twilio has no `rejected` status: declined calls surface as `busy`, `no-answer`, or `failed` + `errorCode`. The caller reports `answered_speech`, `answered_no_speech`, `busy`, `no_answer`, `failed`, `canceled`, or `timeout`. | Person 4 |
| D9 | Manifest additions | `spec.result {renderer, schema}`, `connectors.twilio: [voice.call]`, `knowledge: [search]`, `resources.pids`, and config UI hints `x-crewquarters-widget` (`timezone`, `modelProfile`, `knowledgeBase`, `spreadsheet`, `textarea`) plus `x-crewquarters-group` | Persons 1, 4 |
| D10 | Profile families | The SDK resolves a family (`local.general`) to the single granted variant from the handshake grants, so the broker only ever sees exact variants (§8.1) | Person 2 |
| D11 | Compose profile names | README (`demo-cpu`, `demo-public-callbacks`) and PLAN §14.1 (`callbacks`) disagree. This work uses PLAN's `dev`. | Person 2 |
| D12 | `.gitignore` patterns | Anchor `models/`, `data/`, `var/`, `build/`, `dist/`, `logs/`, `tmp/`, `token*.json` to the repository root. As written they hide any same-named source folder, such as an ORM `models/` package. Person 5 only added `/evidence/` and `/.e2e/`. | All |
| D13 | LLM tools | Not supported in v1alpha1 (`tools` must be empty → 422 `UNSUPPORTED_FEATURE`) | Person 2 |
| D14 | Agent exit codes | 0 = succeeded outcome accepted; 1 = failed or cancelled outcome accepted; 2 = no outcome posted. The broker's recorded outcome is authoritative. | Person 2 |
| D15 | Unclassified digest items | Messages the model leaves out go under **Important** with `needsReview: true`, so they are never hidden in Low | Person 4 |
| D16 | Unowned work | Backup/restore (§15.2), the diagnostics bundle, and the reboot tests (§23.6) have no owner in §17 or §19 | Person 1 (integration lead) |
| D17 | Isolation check | contract-probe passes `isolation` when TCP connects to `1.1.1.1:443`, `host.docker.internal:80`, and `example.com:443` all fail. It does not require DNS to fail, because Docker's embedded DNS may still resolve names on an internal network; "no egress" is the property that matters. | Person 2 |
| D18 | Cancellation in agent code | `crewquarters.errors.Cancelled` subclasses `asyncio.CancelledError`, so `except Exception` can never swallow a cancel. An owner cancel arrives as task cancellation. | Person 3 |
| D19 | Result JSON casing | Agent results are camelCase (for example `processedCount`, `operatorDecision`), matching the rest of the HTTP contracts; the result schemas are in the manifests | Persons 1, 4 |
| D20 | Event types agents may emit | Only `log`, `progress`, `metric`, `artifact`. `status`, `input.*`, `llm.call`, `connector.call`, and `capability.denied` are platform-emitted and never contain prompts or payloads. | Person 1 |

## Consequences

- The SDK, agents, and E2E suite stay usable while the real services are built. contract-probe
  becomes the first acceptance test for each real service (`tests/live`).
- If an owner rejects a draft, the fix is mechanical: update the contract file, run
  `make test-contract` to find every fake route and SDK call affected, and change them.
