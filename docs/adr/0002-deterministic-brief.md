# ADR-0002: The brief is deterministic, not LLM-generated

- **Status:** Accepted
- **Date:** 2026-06-29

## Context

At a session boundary we must hand the next session a summary of where work
stopped. The tempting approach is to ask an LLM to summarise the session log
into a brief. handoff-mcp instead assembles the brief with explicit, rule-based
logic (`brief.build_brief`): filter superseded events, sort by section priority
then importance then recency, then fill a token budget greedily.

## Decision

Build the brief **deterministically**. No model call sits in the path that
reconstructs context for a resuming session.

## Rationale

- **Reproducibility.** The same vault state always yields the same brief. This is
  what makes the reconstruction benchmark meaningful and CI-checkable, and what
  lets a user trust that nothing was silently invented or dropped.
- **No hallucination, no omission risk.** An LLM summariser can drop the one
  decision that mattered or invent a detail. A ranker can only ever surface
  events the user actually logged.
- **Cost and latency.** Session start should be instant and free. A deterministic
  brief is a few SQLite reads and string formatting.
- **Honest token budgeting.** Because we control assembly, we can guarantee the
  brief fits a budget and report exactly how many items were dropped and why —
  rather than hoping a model respected a "be concise" instruction.
- **The hard part is curation, not prose.** The value is *choosing what a
  resuming session needs first* and *excluding what's been retracted*. That is a
  ranking + supersession problem, which rules express better than prompts.

## Consequences

- The brief reads as structured bullet points, not flowing prose. For a hand-off
  consumed by another LLM, that is a feature.
- Ranking quality depends on the `importance` signal and the fixed section order.
  These are simple, inspectable knobs; if they prove too blunt, they can evolve
  without changing the deterministic contract.
- An LLM may still *consume* the brief (via the `resume` prompt) — we just don't
  rely on one to *produce* it.
