# 0001 — Contract drafts and open questions from the Person 5 build

- Status: resolved in the implementation. Person 1's canonical contracts replaced the drafts, and
  `broker-sdk.openapi.yaml` is stable for `v1alpha1` (`x-status: stable`, owner Person 3) and
  served by `services/capability_broker` (D1). The "still open" table records the proposals as
  implemented; see the status update below.
- Author: Person 5 (Vineet Kumar)
- Date: 2026-09-24 (updated 2026-09-25 after integrating the control plane)
- Related: `docs/superpowers/specs/2026-09-24-person5-sdk-agents-qa-design.md`, `packages/contracts/`

## Context

The Person 5 prompt in `PLAN.md` §19 says to build against *frozen* contracts. None existed when
this work started, so Person 5 drafted them. Person 1 has since published the canonical contracts:
`agent-manifest.schema.json`, `capabilities.yaml`, `events/run-event.schema.json`, and the generated
`openapi.yaml` (public `/api/v1` and internal `/internal/v1`), with `crewquarters_shared` as the
reference implementation. Person 5's drafts of those four were removed and everything now follows
Person 1's versions:

- The fake platform reuses `crewquarters_shared` for manifest validation, model-profile binding,
  configuration validation, capability derivation, input-schema guards, and the run state machine.
- The fake's `/api/v1` operations use the canonical paths and shapes, checked by
  `tests/contract/test_fake_route_parity.py` and `test_fake_traffic_conformance.py` (recorded
  traffic from all three agents validates against `openapi.yaml`, and every run event against the
  run-event schema, formats included).
- The bundled manifests pass the control plane's validation, and publish and install on the real
  control API (checked 2026-09-25 against the local Compose stack).

One Person 5 draft remains: `broker-sdk.openapi.yaml`, the API between an agent's SDK and the
capability broker (Person 3). Its run, input, and action operations mirror `/internal/v1` so the
broker can pass them through, adding the attempt from the verified run token.
`tests/contract/test_broker_contract_files.py` checks the mirrored schemas against `openapi.yaml`.

## Status update (2026-09-25, after integration)

- **D1:** the real broker serves every operation in `broker-sdk.openapi.yaml`
  (`services/capability_broker/tests/test_broker_sdk.py::test_broker_serves_every_contract_operation`),
  and the SDK runs against it (`test_broker_with_sdk.py`, `tests/realstack`). The draft marker is
  gone (`tests/contract/test_broker_contract_files.py::test_broker_contract_is_stable_and_names_its_owner`).
- **D2, D5, D10, D13:** implemented as proposed by the broker and the model gateway.
- **D16:** backup/restore, the diagnostics bundle and demo reset exist
  (`docs/runbooks/backup-restore.md`). Reboot is covered at the Compose level only
  (`docs/release/checklist.md`, 23.6).
- **D21:** still applies to the fake platform only. The real platform reads container exits and
  reports `AGENT_OUT_OF_MEMORY`, `AGENT_EXITED` or `AGENT_EXITED_WITHOUT_RESULT`
  (`docs/adr/0006-run-lifecycle-and-time-limits.md`, revision 1).

## Decisions

Resolved by the canonical contracts (kept for the record):

| # | Topic | Outcome |
| --- | --- | --- |
| D3 | Idempotency storage | Resolved: control-plane action keys (`/internal/v1/runs/{id}/actions/{key}/claim|complete`) with statuses `claimed`, `in_doubt`, `completed`. There is no takeover; the SDK raises `OutcomeUnknown` on `in_doubt` unless the caller opts in with `resume_in_progress=True`. |
| D4 | Input-request fields | Resolved: one `preview` object. The SDK sends `{blocks, choices, consequence}` in it, and the answer is `{version, value}`. Reusing a key returns the existing request (no `INPUT_KEY_CONFLICT`); a request an earlier attempt left cancelled or expired is reopened. Answer schemas may not use `pattern`. |
| D9 | Manifest additions | Resolved: `spec.resultSchema` replaces `spec.result`; the renderer id is the `x-crewquarters-renderer` annotation inside it. `knowledge: [config]` (the knowledge base chosen in configuration) and `twilio: [call.fixed_script]` replace `knowledge: [search]` and `twilio: [voice.call]`. All five permission entries are required. |
| D12 | `.gitignore` patterns | Resolved in the merged `.gitignore`. |
| D14 | Agent exit codes | Resolved with the canonical result: exit 0 when the recorded state is `SUCCEEDED`, 1 for any other recorded result, 2 when no result could be posted. |
| D18 | Cancellation in agent code | Kept, and cancellation is reported as `failed` with `RUN_CANCELLED` (not retryable); the control plane records `CANCELLED` because the owner's cancel moved the run to `CANCELLING`. |
| D20 | Event types agents may emit | Resolved: `run.log`, `run.progress`, `run.metric`, `run.artifact`. LLM calls, connector calls, and capability denials are audit records, not run events. |

Still open:

| # | Topic | Proposal (what is implemented today) | Decides |
| --- | --- | --- | --- |
| D1 | Broker SDK API | Adopt or amend `broker-sdk.openapi.yaml`: the mirrored run/input/action operations plus LLM, knowledge, Gmail, Sheets, and telephony routes; capability strings from `capabilities.yaml` (`user_input`, `llm.profile:<variant>` and `cloud.<provider>`, `knowledge.search:config`, `google.gmail.readonly`, `google.spreadsheets`, `twilio.call.fixed_script`, `sip.call.conversational`); voice calls and the model facade are D24 | Person 3 |
| D2 | How configuration reaches the agent | In the handshake response (`config`), which the broker reads from `/internal/v1/runs/{id}` (`InternalRunOut.config`) | Persons 2, 3 |
| D5 | Active-time clock | The control plane pauses the clock in `WAITING_INPUT` and moves the run between `RUNNING` and `LOADING_MODEL` through `/internal/v1/runs/{id}/model-state`; the model gateway must call it. | Persons 2, 3 |
| D6 | Caller `status` column | Blank, `ready`, or `pending` means eligible. `done`, `called`, `skip`, `dnc`, or `do-not-call` is skipped as `status`. Anything else is skipped as `unrecognized_status`. | Persons 1, 4 |
| D7 | Caller result writes | Idempotent `values:update` at `Results!A<row>:H<row>` (the source row) instead of `append`, so write retries can never duplicate. Columns: `source_row, name, phone_masked, call_sid, status, transcript, completed_at, error`. | Person 3 |
| D8 | "Rejected" call state (§12.1) | Twilio has no `rejected` status: declined calls surface as `busy`, `no-answer`, or `failed` + `errorCode`. The caller reports `answered_speech`, `answered_no_speech`, `busy`, `no_answer`, `failed`, `canceled`, or `timeout`. | Person 4 |
| D10 | Profile families | The SDK resolves a family (`local.general`) to the single granted variant, and the handshake also carries `modelBindings`, so the broker only ever sees exact variants (§8.1). Cloud profiles (`openai.*`, `anthropic.*`) are used only by exact name. | Person 2 |
| D11 | Compose profile names | The fake platform and registry run under the Compose profile `fake` on 127.0.0.1:8090 and :5001, next to the control API on :8080. | Person 2 |
| D13 | LLM tools | Not supported in v1alpha1 (`tools` must be empty → 422 `UNSUPPORTED_FEATURE`) | Person 2 |
| D15 | Unclassified digest items | Messages the model leaves out go under **Important** with `needsReview: true`, so they are never hidden in Low | Person 4 |
| D16 | Unowned work | Backup/restore (§15.2), the diagnostics bundle, and the reboot tests (§23.6) have no owner in §17 or §19 | Person 1 (integration lead) |
| D17 | Isolation check | contract-probe passes `isolation` when TCP connects to `1.1.1.1:443`, `host.docker.internal:80`, and `example.com:443` all fail. It does not require DNS to fail, because Docker's embedded DNS may still resolve names on an internal network; "no egress" is the property that matters. | Person 2 |
| D19 | Result JSON casing | Agent results are camelCase (for example `processedCount`, `operatorDecision`), matching the rest of the HTTP contracts; the result schemas are in the manifests | Persons 1, 4 |
| D21 | Crashed-agent error code | The fake reports a container that exits without a result as `INTERRUPTED` with `HEARTBEAT_LOST` (retryable), the code the control plane's reconciler uses, and closes its open questions as `cancelled`. | Person 2 |
| D22 | Speech model profiles | The families `local.stt` → `local.stt.small` and `local.tts` → `local.tts.small` join `DEFAULT_VARIANTS` and `KNOWN_VARIANTS` in `crewquarters_shared/manifest.py`. They are listed in `llmProfiles` the way `local.embedding` is, and the model gateway routes them to the speech server (Parakeet and Kokoro, [docs/voice](../voice/README.md)). | Persons 1, 2 |
| D23 | SIP permission | `connectors.sip: ["call.conversational"]` grants the capability `sip.call.conversational` (manifest schema, `capabilities.yaml`, `capability.py`). A manifest without `sip` means `[]`, so existing installs and approvals are unchanged. | Persons 1, 3 |
| D24 | Broker voice calls and the model facade | Voice calls: `POST /voice/calls`, `GET /voice/calls/{id}`, and `POST /voice/calls/{id}/hangup`. The broker holds the LiveKit key and the SIP trunk. It returns a token for one room and the identity `agent`, valid for the ring timeout plus the maximum duration plus 60 s. Numbers are masked. The OpenAI-compatible facade: `/openai/v1/chat/completions`, `/audio/transcriptions`, `/audio/speech`, and `/models`, with the run token as the API key and granted profile variants as model names. Tools are allowed on this facade only, and `/llm/chat` keeps D13. | Persons 2, 3 |
| D25 | AI disclosure on calls | A conversational calling agent says in its first sentence that it is automated and never denies being an AI. The reference module's "never admit you are an AI" rule was removed, and the agent rejects a `disclosure` that does not say it is automated. Proposal: the platform enforces the same for every agent with `sip.call.conversational` (catalog review, and the approval card shows the disclosure). | Person 1 (policy), Person 4 (approval card) |

## Consequences

- The SDK, agents, and E2E suite stay usable while the broker and runtime are built, and they
  already speak the control plane's semantics. contract-probe becomes the first acceptance test
  for each real service (`tests/live`).
- If Person 3 changes the broker draft, update the file and run `make test-sdk`: the route-parity,
  traffic-conformance, and mirror tests point at every fake route and SDK call affected.
