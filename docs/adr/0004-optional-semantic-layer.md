# ADR-0004: Semantic recall is an optional, pluggable layer

- **Status:** Accepted
- **Date:** 2026-06-29

## Context

Keyword search (FTS5/bm25) misses paraphrased queries that share no literal
terms with the stored text. Embedding-based semantic recall fixes that, but
pulling embeddings into the core would contradict ADR-0001 (SQLite, not a vector
DB) and ADR-0002 (deterministic brief). We want the option without the baggage.

## Decision

Add semantic recall as a strictly **optional layer** that augments — never
replaces — keyword search:

1. **Pluggable embedder.** An `Embedder` protocol selected at runtime by
   `HANDOFF_EMBEDDER` — one server, not a fork per backend:
   - `hashing` (default) — deterministic, dependency-free feature hashing
     (BLAKE2b). Lexical baseline; great for tests and offline use.
   - `openai` — any OpenAI-compatible endpoint (OpenAI, Together, …) via the
     `openai` SDK; lazy-imported, settings from env.
   - `local` — offline `sentence-transformers`.
   All backends L2-normalise their output so the store can treat L2 distance and
   cosine interchangeably. Adding another backend is one class implementing the
   protocol.
2. **BLOB-canonical storage.** Vectors persist as BLOBs in an ordinary SQLite
   table, so the store works with zero extensions and is rebuildable from the
   vault like every other derived artifact.
3. **sqlite-vec as an accelerator, not a requirement.** If `handoff-mcp[semantic]`
   is installed and the extension loads, a `vec0` table provides fast KNN, rebuilt
   from the BLOBs on startup. Otherwise an exact pure-Python cosine scan returns
   the same ranking — fine at personal-memory scale.
4. **Hybrid via Reciprocal Rank Fusion.** `mode='hybrid'` fuses the keyword and
   semantic ranked lists with RRF (`1/(k+rank)`, k=60), which combines them
   without having to reconcile bm25 and cosine score scales.

The whole layer is off unless `HANDOFF_SEMANTIC=1`. The brief stays deterministic
and never consults embeddings.

## Rationale

- **No core regression.** Default installs and the brief are unchanged; the
  determinism and zero-infra properties hold.
- **Honest about quality.** The hashing embedder captures lexical overlap, not
  deep paraphrase. It demonstrates the architecture and is reproducible in CI;
  users who need true semantic recall plug in a learned model — a one-class swap.
- **Same results, optional speed.** Making `sqlite-vec` an accelerator over a
  canonical BLOB store means correctness never depends on an extension that may
  not load on every platform/Python build.

## Consequences

- Two retrieval signals must be fused; RRF keeps that principled and scale-free.
- CI (without the `semantic` extra) exercises the exact-cosine path; the `vec0`
  fast path is covered by a test that skips when the extension is absent.
- `HashingEmbedder` quality is a known ceiling, documented as the intended
  extension point rather than a finished answer.
