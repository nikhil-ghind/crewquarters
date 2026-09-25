# caller

Reads a contacts sheet (`name, phone_e164, consent, status`) and asks the operator to approve the
exact call list. It then places a fixed-script call with a spoken disclosure and a short speech
gather to each approved recipient, and writes one result row per recipient.

- **Who is called:** consent must be exactly `yes`, `true`, or `consented` (case-insensitive). The
  number must be valid E.164. `status` must be blank, `ready`, or `pending`. Rows marked `done`,
  `called`, `skip`, `dnc`, or `do-not-call` are skipped, and so are rows with any other status,
  duplicate numbers, and rows beyond `maxCalls` (default 3, hard maximum 10).
- **Approval:** the operator sees masked numbers (`••••0101`), the disclosure and script, the
  skipped rows with reasons, and an **Approve N calls** button. The request key hashes the
  recipients and script, so a changed sheet needs a new approval.
- **No duplicate calls:** each call is guarded by `ctx.idempotency.once("call:<run>:<row>")`, and
  the broker deduplicates the create by the same key. A retried run reuses the stored approval
  and calls, so it never redials.
- **Result rows:** written with an idempotent update at `Results!A<row>:H<row>`, the same row
  number as the source. A failed write is retried, and retrying a write never redials. Columns:
  `source_row, name, phone_masked, call_sid, status, transcript, completed_at, error`.
- **Privacy:** full numbers never appear in events, logs, results, or sheets. The caller uses no
  LLM.

Busy and no-answer calls are not failures. Only `failed` and `timeout` count as failures.
