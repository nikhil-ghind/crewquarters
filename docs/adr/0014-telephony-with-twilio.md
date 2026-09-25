# ADR 0014: Twilio telephony with a broker-built fixed script

- Status: Accepted
- Date: 2026-09-25
- Owner: Nikhil Sajan Khaneja (Person 3)

## Context

The caller agent phones consenting contacts from a Google Sheet, speaks a disclosed script, and records a short spoken reply (PLAN.md sections 12.1 and 25). Phone calls carry more risk than anything else the platform does:

- A call is a real side effect. It cannot be undone, and an at-least-once job system can repeat it.
- Agent code, and the spreadsheet it reads, are not trusted to decide what a stranger hears.
- Twilio reaches the device through public callbacks, which anyone can forge.
- Full phone numbers are personal data and must not reach logs, events or LLM prompts.
- Nothing here makes a call legally compliant (PLAN.md section 16.1, "Phone abuse").

## Decision

Twilio is the only telephony provider. The capability broker owns the Twilio account, builds every TwiML document and receives every callback. Code: `services/capability_broker/src/crewquarters_broker/twilio.py`, `callbacks.py` and `agent_api.py`.

**The agent cannot write TwiML.**

- The agent calls `POST /internal/v1/sdk/telephony/calls` with `to`, `script`, `gather` and `idempotencyKey`. It needs the `twilio.call.fixed_script` capability, which comes from the manifest's `connectors.twilio: ["call.fixed_script"]`.
- `approved_script()` accepts `script.text` only if it equals the installation's `config.script` with every `{name}` replaced by the same `plain_name()`: 1-40 letters, spaces, apostrophes, hyphens and periods. A spreadsheet cell cannot add a sentence.
- `script.disclosure` must equal `config.disclosure` when one is configured. Otherwise the broker uses its own `DISCLOSURE` constant. Any mismatch is `PERMISSION_DENIED` and no call is placed.
- `voice_twiml()` escapes both strings and always emits the same shape: `<Say>` disclosure first, then `<Gather input="speech" timeout=… speechTimeout="auto">` around the script, then a fixed goodbye. The gather timeout is the agent's `gather.timeoutSeconds`, bounded 1-60 by the request schema.

**Callbacks are signed and matched.**

- The proxy forwards only `POST /api/v1/callbacks/twilio/{voice,gather,status}/{callId}` to the broker (ADR 0010).
- `verify_callback()` recomputes Twilio's HMAC-SHA1 signature over `CQ_PUBLIC_BASE_URL` + path + query and the sorted form fields, and compares it in constant time. The URL is the public one Twilio signed, not the internal request URL (ADR 0009).
- Bodies over 64 KiB are refused with `413` before they are read. A missing or malformed `X-Twilio-Signature` is rejected without decrypting the auth token, and the signing credentials are cached for 60 seconds.
- Rejections return `403 SIGNATURE_INVALID`, increment `cq_broker_callback_rejections_total`, and write at most one `callback.twilio.rejected` audit row per reason per minute.
- The callback's `CallSid` must match the stored `provider_sid`. A signed callback that arrives before the broker has stored Twilio's answer (state `CREATING` or `IN_DOUBT`) adopts its CallSid once, if `To` ends in the call's last four digits.

**Duplicates never redial.**

- `telephony_calls` is unique on `(run_id, idempotency_key)` and on `provider_sid` (migration `services/control_api/migrations/versions/20260925_0003_secrets_connectors_and_knowledge.py`). The same key returns the same call.
- If Twilio's answer to the create request is lost (transport error or 5xx), the call becomes `IN_DOUBT` and the agent gets `503 OUTCOME_UNKNOWN`. It is never retried automatically, and a retry with the same key returns `OUTCOME_UNKNOWN` again. A Twilio 4xx marks the call `failed` with `PROVIDER_REJECTED`.
- The caller agent wraps each call in `ctx.idempotency.once(f"call:{run_id}:{row}", …)`, which is backed by `idempotency_actions`. A Sheets write failure is retried on its own without placing another call.
- Duplicate or late status callbacks never move a call backwards or out of a terminal state (`_advances()`), and the first transcript wins.

**Caps and personal data.**

- `config.maxCalls` (default 3, clamped to 1-10) is checked, and the row inserted, under a per-run `pg_advisory_xact_lock`. Over the cap the call fails with `409 CALL_LIMIT_REACHED`.
- `to` must match E.164 (`^\+[1-9]\d{7,14}$`). With `CQ_PROVIDER_MODE=live`, it must also be in `CQ_TWILIO_ALLOWED_NUMBERS` (default `[]`, so live calls are refused until the operator lists verified numbers).
- The full number is used once, to place the call. The row stores `destination_hash` (HMAC-SHA256 of the number keyed by `CQ_SECRET_KEY`) and `destination_last4`. The `Call` view returns only `toMasked`, and logs and audit rows use `mask_phone()`.
- The transcript is trimmed to 500 characters and stored in `telephony_calls.transcript`.
- The Twilio account SID, auth token and caller number are saved in Connections and encrypted by the secret store (ADR 0007). A separate owner-confirmed test call is limited to one a minute, and in live mode to the allowed numbers.

**Consent and operator approval live in the agent.** `agents/caller` accepts consent only from `yes`, `true` or `consented`, and skips rows with invalid numbers, invalid names, duplicates or rows over the cap. It asks the operator to approve the exact masked list with `ctx.input.ask` before calling.

**Fake Twilio in development.** `CQ_PROVIDER_MODE` defaults to `fake` in `infra/compose/compose.yaml` and to `live` in the appliance file. `fakes.FakeTwilio` answers the REST API through an `httpx.MockTransport`, and `call_simulator()` plays the status, voice and gather callbacks. The last digit of the number picks the outcome: 2 busy, 3 no-answer, 4 failed, 5 answered without speech, anything else answered with speech.

## Alternatives considered

- **Let the agent send TwiML or free text.** This is simpler, but a compromised agent or a crafted cell could make the device say anything to anyone. Rejected.
- **A general telephony abstraction.** Deferred (PLAN.md section 1). One provider keeps the signature and callback rules concrete.
- **Retry an unconfirmed create.** This could ring someone twice. `OUTCOME_UNKNOWN` and callback adoption are safer.
- **Store the number encrypted for later display.** We don't need it back. A keyed hash and the last four digits are enough to deduplicate and to show.

## Consequences

- Live calls need a public HTTPS origin in `CQ_PUBLIC_BASE_URL` that Twilio can reach, usually the `callbacks` tunnel (ADR 0010). A fully offline device cannot receive call results.
- An `IN_DOUBT` call needs an operator to check the Twilio console. The platform will not guess.
- Consent filtering and operator approval are enforced by the caller agent, not by the broker. The broker enforces the script, disclosure, cap, E.164 format and, in live mode, the allowed-number list. Another agent with `twilio.call.fixed_script` could skip the approval step, so this capability is only approved for reviewed agents.
- Divergences from PLAN.md:
  - Section 16.1 mentions timestamp checks for webhooks. Twilio does not sign a timestamp, so replay protection comes from signature, CallSid matching and idempotent state updates.
  - Section 12.1's `responseSeconds` is a config value. The broker takes the gather timeout from the request, and the agent passes `config.response_seconds`.
  - `maxCalls` is clamped at 10 by the broker, while PLAN.md section 2.2 only sets the default of 3.
