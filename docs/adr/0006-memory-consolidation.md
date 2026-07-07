# ADR-0006: Opt-in memory consolidation ("sleep")

- **Status:** Accepted
- **Date:** 2026-06-30

## Context

At scale the episodic layer grows without bound — millions of session notes are
a *volume* problem (disk + index size), not just an indexing one (ADR-0005).
Most of that history is redundant once distilled: what a future session needs is
the lasting knowledge, not every raw log line.

## Decision

Add an **opt-in consolidation** pass — `consolidate(project?, older_than_days?)`,
exposed as an MCP tool and an engine method:

1. Deterministically select finished (`status == done`), non-current sessions
   (optionally older than a cutoff).
2. Distil their **active** events (retracted decisions excluded) into durable
   `(entity, fact)` pairs via an LLM and append them to the entity notes.
3. **Archive** the originals to `<project>/archive/` and drop them from the
   active index — shrinking the vault and index while preserving an auditable
   copy.

The LLM is pluggable behind a `Summarizer` protocol (OpenAI-compatible backend,
mirroring the embedder design) and is **off unless `HANDOFF_LLM_MODEL` is set**.

## Rationale

- **Consolidation is the bridge between the two memory layers** the system
  already has (episodic sessions ↔ durable entities) — it formalises "fold the
  log into knowledge".
- **The one LLM write-path, quarantined.** This is the *only* place an LLM writes
  memory; the brief, search, and supersession stay deterministic. Distilling only
  *active* events means the LLM never re-introduces a decision supersession
  retired.
- **Explicit primitive, not a hot-path surprise.** Consolidation is paid, slow,
  and lossy, so it is an explicit operation. An opt-in auto-trigger can later
  layer on top (running in the background / at startup, never blocking
  `checkpoint`); candidate detection is deterministic and cheap, so the system
  can always *report* "N sessions are consolidatable" without calling the LLM.
- **Auditable and reversible.** Originals are archived, not deleted, so a bad
  distillation is recoverable and the source of truth is never lost.

## Consequences

- A configured LLM endpoint and tokens are required to run it; without one the
  tool is a no-op that says so.
- Distillation is lossy by design — fidelity depends on the model. Spot-checked
  once against a live model on one scenario (it kept the active "use SQLite"
  decision, dropped the retracted "use JSON" one, and preserved a dead-end as a
  caution) — not a repeatable regression guard. A harness for scoring this
  systematically per-model exists (`benchmarks/consolidation_eval.py`), but its
  results aren't published yet.
- Archived sessions are no longer searchable in the active store (by design); a
  future "search archive" affordance could restore them read-only if wanted.
