# ADR-0001: SQLite + FTS5 for the index, not a vector database

- **Status:** Accepted
- **Date:** 2026-06-29

## Context

handoff-mcp needs a queryable index over events to power three things: ranking
for the brief (recency + importance), full-text recall across projects, and
temporal/supersession queries. The obvious "memory for LLMs" default is a vector
database (Chroma, Qdrant, pgvector, or the embeddings-first approach of mem0 /
OpenMemory).

## Decision

Use **SQLite with the built-in FTS5 extension** as the derived index. Treat
semantic / embedding search as an **optional** extra (`pip install
handoff-mcp[semantic]`, backed by `sqlite-vec`), never as the core.

## Rationale

- **Zero infrastructure.** SQLite is embedded in the Python standard library and
  FTS5 ships with it. No server, no container, no network — it runs wherever
  Claude Code runs. A vector DB would add an external dependency to a tool whose
  whole value is being frictionless to install.
- **The core queries are exact, not fuzzy.** "Most recent active decisions for
  project X, ranked by importance, excluding superseded ones" is a `WHERE` +
  `ORDER BY`, not a nearest-neighbour search. Forcing this through embeddings
  would make the brief approximate and non-reproducible.
- **Determinism and benchmarkability.** The brief must be reproducible (see
  ADR-0002). FTS5/bm25 is deterministic; ANN indexes and embedding drift are not.
- **Supersession needs identity, not similarity.** Our differentiator is that a
  new decision *retracts* an old one by id. That is a graph/relational fact.
  Embedding stores have no native notion of one fact retiring another — which is
  exactly why similarity-based stores surface stale, contradicted memories.
- **It's rebuildable.** The index is derived from the markdown vault, so SQLite
  being a single file is a virtue: delete it and it's reconstructed.

## Consequences

- Recall is lexical by default. Paraphrased queries that share no keywords with
  the stored text won't match. The `[semantic]` extra adds `sqlite-vec` +
  embeddings for fuzzy recall when wanted — staying in the same SQLite file.
- We own the ranking logic rather than delegating it to a vector store. That is
  intentional: ranking is the product.
- Scale ceiling is whatever SQLite *itself* handles (millions of rows) — far
  beyond a personal cross-project memory's needs. That's the storage engine's
  ceiling, not this app's tested ceiling: the app isn't tuned or benchmarked
  for millions of *events* (see README Limitations and ADR-0005).
