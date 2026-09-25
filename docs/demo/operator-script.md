# Operator script

This script has two parts:

- **Part A** runs Person 5's deliverables on a laptop against the fake platform. It works today.
- **Part B** is the GB10 live rehearsal for the `PLAN.md` §24 steps that involve the SDK agents. It
  needs the real platform from Persons 1–4 and live credentials.

## Part A — laptop demo (fake platform)

**Prerequisites:** Docker Desktop (or Docker Engine with buildx), `uv`, GNU `make`, and about 2 GB
of disk for images. No GPU, Google, or Twilio account is needed.

| # | Step | Command | Show |
| --- | --- | --- | --- |
| 1 | Install the workspace | `make sync` | Python 3.12 venv with the SDK, crewctl, fake platform, and agents |
| 2 | Run the offline suites | `make lint && make test` | Unit, contract, and integration tests green |
| 3 | Start the stack | `make dev-up` | `fake-platform` (127.0.0.1:8080) and `registry` (127.0.0.1:5001) healthy |
| 4 | Build, pin, and run in hardened containers | `make e2e` | Images pushed by digest to the local registry, pins in `.e2e/manifests/`, 6 E2E tests green |
| 5 | Load the demo data | `make demo-seed` | 25 synthetic emails dated yesterday, a contacts sheet, Twilio outcomes, and knowledge docs |
| 6 | Contract probe | `make demo-run AGENT=contract_probe RUN_ARGS='--auto-answer probe-input-*=ok'` | Every check `passed`. The `isolation` detail shows uid 10001, a read-only root, and no egress. |
| 7 | Gmail digest | `make demo-run AGENT=gmail_digest` | Urgent (security alert, outage), Important with 2 flagged "Needs review", Low. Promotions and out-of-window mail excluded. The injection email is harmless and sits in Low. |
| 8 | Caller: cancel first | Terminal A: `make demo-run AGENT=caller`. Terminal B: `make demo-pending`, then `uv run --all-packages crewq-fake answer --choice cancel` | The approval preview shows masked numbers (`••••0101`), the disclosure and script, and skipped rows (consent `no`, invalid number). Cancel gives `operatorDecision: cancelled` and **zero calls**. |
| 9 | Caller: approve | A: `make demo-run AGENT=caller`. B: `make demo-approve` | 3 calls: speech captured, answered without speech, busy (not a failure) |
| 10 | Check the results sheet | `curl -s localhost:8080/fake/v1/state/sheets \| python -m json.tool` | `Results` rows at the source row numbers with masked numbers, never full ones |
| 11 | Failure injection | `curl -s -XPOST localhost:8080/fake/v1/faults -H 'content-type: application/json' -d '{"target":"broker.sheets.update","mode":"error","count":6,"status":503}'`, then repeat step 9 | Warnings are logged, rows are still written, and each number is called exactly once (`/fake/v1/state/calls`) |
| 12 | Collect evidence | `make evidence` | `evidence/<UTC>/report.md`: commit, host, suite results, pinned digests |
| 13 | Reset between rehearsals | `make demo-reset` | Runs, calls, sheets, and faults cleared; images kept |

Optional: point the digest at a real local model instead of mock rules.

```bash
export CREWQ_FAKE_LLM_BASE_URL=http://host.docker.internal:11434/v1
export CREWQ_FAKE_LLM_MODEL=llama3.2:3b
make dev-up
```

## Part B — GB10 live rehearsal (Person 5 steps of PLAN.md §24)

**Prerequisites:** these are owned by the teammates named below and must be in place first.

- Real platform installed on the GB10 by Person 2, with the UI from Person 4.
- A Google test account connected by Person 3. Google test-mode authorisations expire after
  **7 days**, so reconnect before the event.
- Twilio credentials and the callback tunnel configured by Person 3, with **three verified,
  consenting team numbers**.
- A pinned small local model installed by Person 2.
- Agent images built with `make images` and pushed to the appliance registry. The digests go into
  the manifests at release time.

| §24 step | What to do | Pass condition |
| --- | --- | --- |
| 3 | Connect Google in Connections | Gmail and Sheets permissions shown separately; no token visible anywhere |
| 4 | Add Twilio and check the callback status | The test call list contains only the 3 verified numbers, all consent = yes |
| 8 | Install **Daily Gmail Digest**; set the timezone; schedule 10:00; also **Run now** | Cold model load shown; grouped previous-day digest; every item links to Gmail; the lease is released afterwards |
| 9 | Install **Caller**; set the sheet, `Results!A:H`, and the disclosed script; run it; **cancel** once, then rerun and **Approve 3 calls** | Cancel places zero calls; the approval card shows masked numbers, consent, script, and the count |
| 10 | Answer one call; trigger the duplicate-callback fixture (Person 3) | Transcript and status appear in `Results`; no duplicate row or call |
| 11 | Run contract-probe with an approved cloud profile, then disable the profile | Cloud badge and audit event shown; local runs never fall back to cloud |

Run `pytest -m live tests/live` with the `CREWQ_LIVE_*` variables set as described in that file.
This exercises the same agents against the real control API.

**Never** rehearse against personal production Gmail, or against numbers that have not been
verified and have not consented. Calling, recording, privacy, and do-not-call rules need separate
legal review before any real use (`PLAN.md` §12.1).
