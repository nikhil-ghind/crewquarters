# 0001. Record architecture decisions

- Status: Accepted
- Date: 2026-09-24
- Owner: Nikhil Hiro Ghind (Person 1)

## Context

Five people build Crewquarters in parallel against frozen contracts (PLAN.md sections 1 and 20). Decisions that cross service boundaries have to be written down, so that no one reverses them by accident and new contributors can see why they were made.

## Decision

- Architecture decisions are recorded as ADRs in `docs/adr/`.
- Files are numbered sequentially: `NNNN-short-title.md`. Numbers are never reused.
- Each ADR has a **Status**, **Context**, **Decision**, and **Consequences** section.
- Status is one of `Proposed`, `Accepted`, `Superseded by NNNN`, or `Rejected`. An accepted ADR is never edited in substance. To change a decision, write a new ADR that supersedes it.
- An ADR that changes a shared contract is reviewed by every affected owner (PLAN.md section 20, rule 1).
- The fixed v1 decisions in PLAN.md section 1 each need an ADR before the final gate (PLAN.md section 23.1).

## Consequences

- Reviewers can point to one document for a design question.
- Small implementation choices stay in code comments and module docstrings, not ADRs.
