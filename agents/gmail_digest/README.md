# daily-gmail-digest

Summarises the previous local calendar day's Gmail into **Urgent**, **Important**, and
**Low priority** groups. Each item has a reason, a suggested next action, and an **Open in Gmail**
link.

- **Day window:** covers every instant whose local date is "yesterday" in the configured IANA
  timezone. It is exact across DST changes, including zones where midnight does not exist.
  Scheduled runs use `scheduledFor`, so a late run still digests the right day.
- **Reading mail:** paginates Gmail up to `maxMessages` and sets `truncated` when more mail exists.
  Messages are parsed with the SDK's safe MIME/HTML parser, and quoted reply history is removed.
- **Classifying (map):** batches of `batchSize` messages are sent to a local model with
  structured output. Each email is wrapped in an untrusted evidence block with a per-run random
  boundary, and the model sees refs `m1..mN` rather than Gmail ids. An invalid response gets one
  repair retry.
- **Grouping (reduce):** plain code, not the LLM, so every item maps to a fetched message.
  Unknown refs are dropped; messages the model left out go under Important with `needsReview`.
- **Prompt injection:** the agent gives the model no tools and holds only the `gmail.readonly` and
  `llm.local` capabilities, so email text cannot trigger any action.

Result renderer: `crewquarters.gmail-digest/v1` (schema in `manifest.yaml`).

```bash
crewctl test agents/gmail_digest            # default scenario (relative dates → always "yesterday")
```
