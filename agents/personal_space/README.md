# personal-space

Builds a **personalized brief from the owner's own knowledge base**: what the collection holds, what
stands out for this owner, suggested next steps, and questions to ask next. Every theme and highlight
cites passages the agent actually retrieved.

The knowledge base can hold anything (personal notes, papers, recipes, manuals), so the agent does not
assume a profile document exists.

- **Intent:** the config note `intent`, else a Crew Request ("What do you want from your space?").
  No answer in `intentWaitSeconds` means no intent, so scheduled runs never hang.
- **Discovery:** an agent can only search, not list documents. Round one runs broad probes plus the
  intent. Round two asks the model for follow-up queries and adds the headings and document names already
  seen. Passages below `minRelevance` are dropped, because the broker applies no cutoff.
- **Guard:** fewer than `minEvidencePassages` passages gives `status: insufficient_context` with
  suggestions for what to add. The model is not asked to write anything.
- **Synthesis:** one structured call with refs `p1..pN`. Code maps refs back to citation ids and drops
  any theme or highlight without a valid citation.
- **Degraded:** if the model cannot produce a usable brief (invalid output after one repair, or nothing
  citable), the result shows the passages grouped by document with `degraded: true`. The dev mock model
  always lands here; real output needs a real model.
- **Prompt injection:** passages appear only in evidence blocks in the user message, with a per-run random
  boundary. The agent gives the model no tools and holds only `llm.profile:local.general`,
  `knowledge.search:config` and `user_input` (plus events and idempotency), so document text cannot
  trigger any action.

Result renderer: `crewquarters.personal-space/v1` (schema in `manifest.yaml`).

```bash
crewctl test agents/personal_space                     # default scenario
crewctl test agents/personal_space --scenario research # a different kind of knowledge base
```

`minRelevance` is a cosine cutoff. The fake platform scores with BM25, so the scenarios set it to 0.
