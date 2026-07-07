# ADR-0007: Supersede-by-best-match

- **Status:** Accepted
- **Date:** 2026-06-30

## Context

Temporal supersession (ADR-0002, `supersession.py`) is the core differentiator:
a new event retires an old one by listing its id in `supersedes`, and retired
events drop out of the brief. This is precise and auditable — but it assumes the
caller *has the id* of the event being retired.

In practice an agent rarely does. The id (`ev_3f9a…`) was returned in an earlier
session and is long gone from the current context window. So when the agent
genuinely changes a past decision — exactly the case supersession exists for — it
often can't fill in `supersedes`, and the stale decision lingers in the brief.
The mechanism worked; the ergonomics defeated it.

## Decision

Add an **opt-in** `supersedes_query` parameter to `log_event` (engine + MCP
tool). When provided, the engine resolves it to the id of the single
best-matching event and unions that into `supersedes`. Resolution is:

- **Deterministic** — ranks the project's events by bm25 relevance to the query
  using the existing keyword index (no LLM, no embeddings, reproducible).
- **Active-only** — already-superseded events are skipped, so it retires the
  *live* decision, not a previously-retracted one.
- **Same-type-only** — a new `decision` retires a prior `decision`, never an
  unrelated `goal`/`question` that happens to share words. Like-for-like is the
  predictable, low-surprise rule.
- **Top-1** — it retires exactly one event, the best match. It never fans out
  across several events on a fuzzy match.
- **Honest on a miss** — if nothing matches, it retires nothing rather than
  guessing, and the tool response says so.

The resolved id is written into the new event's `supersedes` and persisted in the
vault note, so the retraction is **fully auditable** after the fact — identical on
disk to an explicit-id supersession. The MCP tool's response adds a second line
naming what was retired (`Superseded by best match: ev_… «snippet»`) or noting
that nothing matched.

The explicit-id path is untouched: `supersedes=[...]` behaves exactly as before,
and the two can be combined (their results are unioned).

## Rationale

- **Closes the UX hole without sacrificing auditability.** The result on disk is
  the same explicit edge; only the *way the id was found* is new.
- **Deterministic by construction.** Reusing bm25 keeps the whole supersession
  story reproducible and CI-checkable — no model in the loop.
- **Conservative defaults.** Same-type, active-only, top-1, no-match-is-noop all
  bias toward retiring too little rather than wrongly retiring the wrong thing —
  a silent over-retraction would be worse than a missed one the agent can redo.

## Consequences / limitations

- Keyword (bm25) resolution shares the keyword layer's blind spot: a query that
  shares no literal tokens with the target won't find it (the agent can still pass
  an explicit id, or rephrase). We deliberately did **not** route resolution
  through the optional semantic layer, to keep it always-available and
  deterministic regardless of configuration.
- Top-1 means a decision that genuinely retires *two* prior decisions needs two
  calls or explicit ids. Acceptable for the common single-retraction case.
- The agent still has to *decide* that it is superseding something and pass the
  query — handoff never infers retractions on its own (see ADR-0002). This lowers
  the cost of acting on that intent; it does not remove the intent.
