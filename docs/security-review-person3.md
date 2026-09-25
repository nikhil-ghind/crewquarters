# Security and privacy review: secrets, broker, knowledge

Reviewer: Nikhil Sajan Khaneja (Person 3), as the secondary duty in PLAN.md section 17. Date: 2026-09-25. Branch: `feature/person3-data-connectors`, rebased on main `2c14eb7`.

**Scope.** `packages/secret_store`, `services/capability_broker`, `services/knowledge`, and `packages/contracts/broker-sdk.openapi.yaml`.

**Method.** I checked each PLAN.md section 16.1 threat that touches this scope against the code. I ran the section 16.2 release checks, audited every log call, and probed the suspected issues before fixing them. Every fix has a regression test (`test_broker_security.py`, `test_knowledge_security.py`).

## Summary

| ID | Severity | Finding | Status |
| --- | --- | --- | --- |
| F1 | Medium | Action keys `.` / `..` passed the key pattern, and httpx resolves dot segments, so an agent could make the broker send a service-token request to a different control-API path (`/runs/R/actions/../claim` becomes `/runs/R/claim`). The only reachable suffixes, `/claim` and `/complete`, match no run route today, so the impact was limited to 404s. | **Fixed**: keys must start with a letter or digit |
| F2 | Medium | No request-body limit on the broker. Agents are untrusted and could send arbitrarily large JSON. | **Fixed**: 413 above `CQ_MAX_BODY_BYTES`, checked by Content-Length and while streaming |
| F3 | Medium | Knowledge uploads were spooled to disk in full before the 25 MiB check, so one request could fill the disk. | **Fixed**: the same middleware refuses early; uploads get `CQ_MAX_UPLOAD_BYTES` + 64 KiB, other routes the small limit |
| F4 | Medium | Ingestion failures were logged with a full traceback. Database errors embed SQL parameters, which are document text. | **Fixed**: logs carry the error type only. The platform-wide fix is H1 |
| F5 | Low | A pathological document could keep the ingestion job busy indefinitely, and heartbeats kept its lease alive. | **Fixed**: `CQ_EXTRACT_TIMEOUT_SECONDS` (300) fails the document with `EXTRACTION_TIMEOUT` (residual risk R1) |
| F6 | Low | The configured `knowledgeBaseId` is used as a path segment on the knowledge service without format checks. | **Fixed**: it must be a UUID, otherwise `NEEDS_CONFIGURATION` |
| F7 | Low | Stored documents were created with the default umask (typically world-readable). | **Fixed**: files `0600`, directories `0700` |
| F8 | Low | Live mode would start with a plain-HTTP public URL, which breaks Twilio signature assumptions and Google's redirect rules. | **Fixed**: startup refuses non-HTTPS in live mode, except `localhost` |
| F9 | Info | `/openapi.json` was served without authentication, giving agents a map of the internal APIs. | **Fixed**: disabled on both services |

No Critical or High findings. Nothing was found that lets an agent obtain a credential or use an undeclared operation.

## PLAN.md section 16.1 threats in scope

| Threat | Mitigation in this scope | Evidence |
| --- | --- | --- |
| Secret theft | Envelope encryption, one data key per secret, AAD bound to row, provider and owner. The broker decrypts only Google and Twilio secrets; the gateway only OpenAI and Anthropic. Secrets are never returned by any API. The master keyring file is refused if other users can read it. | `test_secret_store*.py`, `test_broker_connections.py`, `test_no_secret_reaches_the_logs` |
| OAuth CSRF and code theft | Single-use 10-minute state (only its hash is stored), a browser-binding cookie, PKCE (S256), exact redirect URI, and a fixed return URL (no open redirect). | `test_broker_google.py` (replay, expiry, binding, PKCE) |
| Webhook spoofing and replay | Twilio signature over the configured public URL, CallSid must match the call, callbacks never move a call backwards, and the first transcript wins. | `test_broker_twilio.py`, reference vector `0/KCTR6DLpKmkAf8muzZqo1nDgQ=` |
| Prompt injection | Retrieved text is escaped and wrapped in labelled evidence; Sheets writes use `RAW`; the capability checks don't depend on model output. | `test_passage_text_cannot_forge_evidence`, `test_sheets_input_is_raw` |
| Resource exhaustion | Body limits (F2, F3), extraction limits (PDF pages and stream caps, docx expansion, CSV rows, timeout F5), `topK` ≤ 50, and input long-poll ≤ 30 s. | `test_*_security.py`, `test_knowledge_extract.py` |
| Duplicate side effects | Calls are unique per (run, key), a lost Twilio response is `OUTCOME_UNKNOWN` and never redialed, and the broker never retries a Sheets append. | `test_duplicate_start_places_one_call`, `test_lost_response_is_in_doubt_and_never_redialed` |
| Phone abuse | Only the owner-approved script and disclosure, a per-run call cap, the live-mode allowlist, test calls only after confirmation and at most one a minute, and no full numbers stored or logged. | `test_only_the_approved_script_is_spoken`, `test_full_number_never_stored`, test-call tests |
| Agent overreach | The token must be the current attempt's, the capability must be in both the token and the current approval, resources come from config, and `RUN_CANCELLED` applies after a cancel. | `test_broker_auth.py`, `test_broker_security.py` |

## PLAN.md section 16.2 release checks in scope

| Check | Result |
| --- | --- |
| Dependency vulnerabilities | `pip-audit` over the 53 pinned runtime packages (linux/arm64 resolution): no known vulnerabilities (2026-09-25) |
| Secret scan | CI `secret-scan` (gitleaks) passes on PR #3; fixtures use `example.com`, `+1555555xxxx` and low-entropy placeholders |
| Negative authorization for every broker capability | `test_every_capability_operation_requires_its_capability`, `test_cancel_stops_capability_operations_but_not_baseline` |
| Fuzz and limit tests for uploads and webhook bodies | Oversized and streamed bodies, malformed, encrypted and scanned PDFs, zip bombs, malformed CSV, traversal names, forged signatures |
| Log redaction audit | Every log call in scope carries only codes, UUIDs, and status codes (F4 fixed the one leak path). Metrics labels are route templates, never IDs or numbers (`test_broker_metrics.py`) |
| Parser safety | `python-docx` parses with `resolve_entities=False` (no XXE); `pypdf` 6.19 caps decompressed streams at 75 MB |

## For other owners (not in my paths)

| ID | Owner | Recommendation |
| --- | --- | --- |
| H1 | Person 1 | Create the shared engine with `hide_parameters=True` (`crewquarters_shared/db/base.py::create_engine`). SQLAlchemy error messages otherwise embed SQL parameters, including personal data and, in the audit example seen during development, metadata, in every service's logs. |
| H2 | Person 1 | Refuse to start outside the `dev` profile while `CQ_SECRET_KEY`, `CQ_CAPABILITY_SIGNING_KEY` or `CQ_INTERNAL_SERVICE_TOKEN` still hold the documented insecure defaults. The broker already refuses a missing `CQ_MASTER_KEY_FILE`. |
| H3 | Person 1 | Set the OAuth binding cookie exactly as specified in the handoff: `HttpOnly; SameSite=Lax; Path=/api/v1/connections/google; Max-Age=600`, plus `Secure` under HTTPS. |
| H4 | Persons 2, 5 | The reverse proxy forwards only `/api/v1/connections/google/callback` and `/api/v1/callbacks/twilio/` to the broker, with body and rate limits. Agent networks reach only the broker. |
| H5 | Person 1 | Raise the body limit only on the knowledge upload route when forwarding uploads (25 MiB + framing); keep 2 MiB elsewhere. |
| H6 | Process | The Person 3 guard hook loads only when Claude Code starts in `crewquarters/`; start sessions there. |

## Residual risks (accepted for the demo)

- **R1.** A timed-out extraction thread cannot be killed; it finishes in the background. The page, stream, row and expansion limits bound the work.
- **R2.** Overwriting deleted documents is best effort on SSD and copy-on-write filesystems; the appliance relies on full-disk encryption.
- **R3.** Pending OAuth sign-ins live in one broker process; a restart makes the owner click Connect again. The broker must run as a single instance.
- **R4.** The knowledge service trusts its internal callers for knowledge-base authorization. It is reachable only on the private network with the service token.
- **R5.** A captured, validly signed Twilio callback can be replayed. Replays are harmless because callbacks never regress state and the first transcript wins, and Twilio signatures carry no timestamp.
- **R6.** Host root can read the master key (PLAN.md section 16.1). Backups without the key cannot restore connections; backups with it are sensitive.
- **R7.** Nothing here makes calls legally compliant. Live calls are limited to consenting, verified team numbers.
