# contract-probe

A contract-test agent. It exercises each broker capability the SDK exposes and reports every check
as `passed`, `failed`, or `skipped`. The run fails with `CONTRACT_CHECKS_FAILED` if any check
fails, and the full report is included in the error details.

| Check | Passes when |
| --- | --- |
| `handshake` | Run metadata is present; a scheduled run carries `scheduledFor` |
| `events` | 60 log events and progress 0→100 are accepted |
| `input` | An operator input request round-trips (`ok`) |
| `llm` / `structured` | A local profile answers, with a response that parses into a model |
| `knowledge` | A search on `knowledgeBaseId` returns cited passages |
| `idempotency` | `once()` runs the guarded action exactly once |
| `permissions` | Undeclared Gmail access is denied |
| `isolation` | The agent runs as non-root with a read-only root and writable `/tmp`, and has no egress (only meaningful in the hardened container) |
| `cancellation` | Always runs last. Waits for an owner cancel; the run ends `CANCELLED` |

Use it against the real platform once Persons 1–3 land their services. Its checks double as
acceptance tests for the draft contracts.

```bash
crewctl test agents/contract_probe                    # process mode, isolation skipped
crewctl test agents/contract_probe --docker --scenario docker   # hardened container (needs make dev-up)
```
