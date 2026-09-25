# Idempotency: external effects under at-least-once execution

Runs can be retried, either after `INTERRUPTED` (a container or host restart) or after a retryable
`FAILED`. A retry starts a new **attempt** of the same run. Anything your agent does outside
Crewquarters, such as a phone call, an email, or a sheet write, must stay correct if the code runs
again.

## `ctx.idempotency.once`

```python
call = await ctx.idempotency.once(
    f"call:{ctx.run.id}:{row}",       # run-scoped key, shared by every attempt of this run
    create_the_call,                  # async callable, called at most once per key
    result_type=Call,                 # stored as JSON; a replay returns the same type
    resume_in_progress=True,          # only if create_the_call is itself idempotent at the provider
)
```

`once` claims the key through the control plane's action records (`/actions/{key}/claim`), runs
`fn`, and completes the key with the result. The claim answers one of three ways:

| Claim status | Meaning | What `once` does |
| --- | --- | --- |
| `claimed` | This call created the key. | Runs `fn`, stores its result (`completed`), and returns it. |
| `completed` | An earlier call finished. | Returns the stored result. `fn` is **not** called. |
| `in_doubt` | The key was claimed before (by this or an earlier attempt) and never completed, so the effect may already have happened. | Raises `OutcomeUnknown`, unless `resume_in_progress=True`, which runs `fn` again and completes the key. |

Only use `resume_in_progress=True` when repeating `fn` cannot duplicate the effect. The caller
agent does this because the broker deduplicates call creation by the same `idempotencyKey`.
`claim(key)` and `complete(key, result)` are available for flows that need the steps separately.

## Patterns the bundled agents use

- **Provider-side deduplication plus `once`.** The caller creates each call with
  `idempotencyKey = "call:<run>:<row>"` inside `once`. Neither a retried attempt nor a timed-out
  create can place a second call.
- **Fixed ranges instead of appends.** Sheets `append` is not idempotent. A timeout leaves you not
  knowing whether the row was written, and repeating it duplicates the row. The caller writes each
  result with `update_values` to `Results!A<row>:H<row>`, the same row number as the source contact,
  so repeating a write is harmless and never redials.
- **Stable input keys.** `ctx.input.ask(key=…)` is idempotent per run, so a retried attempt gets
  the stored answer. A question an earlier attempt left open is cancelled when that attempt ends,
  and asking the same key again reopens it. Derive the key from what the operator approved; the
  caller hashes the recipients and script. That way a changed plan needs a new approval, and an
  unchanged one does not ask twice.
- **Let the SDK ride out platform restarts.** Calls whose request never left the container are
  retried for up to the heartbeat timeout, and so are idempotent calls whose connection broke.
  A non-idempotent call whose connection broke after sending raises `OutcomeUnknown` instead
  (see *Transport retries* in [reference.md](reference.md)).
- **Treat `OutcomeUnknown` as a real state.** Record it for the operator and do not guess. Never
  repeat a non-idempotent action automatically from agent code.
