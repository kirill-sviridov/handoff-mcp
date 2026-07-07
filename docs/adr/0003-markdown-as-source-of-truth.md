# ADR-0003: The markdown vault is the source of truth

- **Status:** Accepted
- **Date:** 2026-06-29

## Context

Memory has to live somewhere durable. The conventional choice for a memory
backend is a database (the SQLite index, or a vector store) as the primary
store, with any human-readable export being secondary. handoff-mcp inverts this:
a directory of **markdown notes is authoritative**, and the SQLite index is a
disposable, rebuildable derivative.

## Decision

Persist every event first to a human-readable markdown note. The SQLite + FTS5
index is built *from* the vault and can be regenerated at any time.

## Rationale

- **User ownership and portability.** The memory is plain files on the user's
  disk, in a format they can read, grep, edit, version-control, and back up. No
  lock-in to our schema or to a running service.
- **Obsidian-native.** The vault opens directly in Obsidian. `[[wiki-links]]`
  connect episodic session notes to durable project-knowledge entities — across
  projects — turning the memory into a navigable knowledge graph the user can
  explore and curate by hand.
- **Auditability and trust.** A user (or a hiring reviewer) can open a note and
  see exactly what was recorded and why a decision was retracted. A binary DB
  blob offers none of that.
- **Resilience.** Because the index is derived, corruption or schema changes are
  non-events: delete `.index.db` and it rebuilds from the notes on next start.
- **Human + machine edits coexist.** A user can hand-edit an entity note in
  Obsidian and the next index rebuild picks it up.

## Consequences

- Writes touch the filesystem and must round-trip losslessly through markdown,
  so the note format is a real contract (covered by `test_vault.py`). Machine-only
  fields (`id`, `supersedes`) live in trailing HTML comments to stay invisible in
  rendered markdown while remaining parseable.
- Markdown parsing is stricter and a little slower than reading a DB row. For a
  personal-scale memory this cost is negligible, and the index absorbs the hot
  read paths anyway.
- Two representations exist (notes + index) and must stay consistent. The rule
  "vault is truth, index is rebuilt on startup" keeps that one-directional and
  simple.
