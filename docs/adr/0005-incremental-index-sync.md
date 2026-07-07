# ADR-0005: Persistent, incrementally-synced index

- **Status:** Accepted
- **Date:** 2026-06-30

## Context

The SQLite index is derived from the markdown vault (ADR-0001/0003). The first
implementation rebuilt it from scratch on every startup: read and re-parse every
session note, re-insert every event. That is fine for personal scale (hundreds–
thousands of sessions) but becomes the bottleneck for a long-lived, large vault —
startup cost grows O(total history) on every launch.

## Decision

Keep the index file across runs and **sync it incrementally** on startup:

- Each session note records a `(mtime, size)` signature in an `indexed_sessions`
  table. On startup only notes whose signature changed are re-parsed and
  re-ingested (`replace_session_events`); notes that disappeared are dropped.
- Events are immutable (a change is a new event that supersedes the old), so an
  already-indexed event never needs re-reading.
- A deleted `.index.db` self-heals — every note looks new and is re-ingested.
- The optional semantic store is fed from the synced index (fast) rather than
  re-parsing the vault, and embedding is itself incremental (ADR-0004).

The connection is opened in WAL mode with a busy timeout so concurrent sessions
on one vault coexist instead of failing with "database is locked".

## Rationale

- **Startup scales with *new* work, not total history** — the common case (a few
  new events since last launch) re-parses only the current session's note.
- **Correctness is preserved** — the vault stays the single source of truth; the
  index is still fully rebuildable, just not rebuilt wholesale each time.
- **Same trick, applied consistently** — mirrors the incremental embedding cache,
  so the system has one coherent "derive only what changed" story.

## Consequences

- A small amount of bookkeeping (the `indexed_sessions` table, signature checks).
- `(mtime, size)` is a heuristic; a content edit that preserves both would be
  missed. In practice notes are append-only via the API, and a manual editor
  changes mtime — acceptable, and a full `rebuild()` remains available.
- **Remaining scale ceiling (documented, not yet addressed):** the brief still
  loads a whole project's events into memory to rank them, and a single
  `sessions/` directory with millions of files is slow to enumerate. Both are
  fine at personal/team scale; if a server-scale deployment ever needs them, the
  fixes are localized (push ranking into SQL with `LIMIT`; shard session files by
  date) and do not require changing the vault-as-truth architecture.
