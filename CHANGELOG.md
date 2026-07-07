# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [0.3.1] — 2026-07-07

### Fixed
- `checkpoint` now accepts a `project` parameter, mirroring the other memory
  tools (`log_event`, `get_brief`, `note_entity`, `consolidate`). Previously it
  used the session's default project unconditionally, so a session that logged
  its work under `project="X"` left that session note open and returned the
  wrong (empty default) brief.

## [0.3.0] — 2026-07-07

### Added
- **Multi-device sync** (`sync` tool + `handoff-sync` CLI) — a thin, opt-in git
  layer over the vault: pull, commit, and push a private git remote so memory
  follows you across machines. `handoff-sync --setup <url>` configures the remote
  (writing a union-merge `.gitattributes` and gitignoring the derived index); set
  `HANDOFF_AUTO_SYNC=1` for pull-on-brief / push-on-checkpoint. Core memory stays
  fully functional with zero git.
- Entity-note conflicts across machines resolve automatically via git's built-in
  `union` merge driver — no custom merge engine.
- **Tool-surface CI smoke gate** (`scripts/check_tool_surface.py`) pinning the MCP
  tool schemas and read-only/destructive annotations against drift.

### Changed
- Git subprocesses are bounded by a timeout and refuse interactive credential
  prompts, so a slow or unauthenticated remote can never block a session.

## [0.2.0] — 2026-07-06

### Added
- **Memory consolidation** (`consolidate` tool) — opt-in LLM "sleep" pass that
  distils old finished sessions' active decisions into durable entity notes and
  archives the originals, to tackle memory *volume* at scale (ADR-0006).
- **Deterministic reranking** of recall — `search_memory` reorders results by a
  relevance + recency (time-decay) + importance blend; `rerank=False` for raw
  order.
- **Importers** — `handoff-import` CLI bootstraps a project's memory from a git
  commit history (deterministic) or a Claude Code transcript (best-effort);
  idempotent re-import.
- **Pluggable embedding backends** — `hashing` (default, zero-dep), `openai`
  (OpenAI-compatible incl. a self-hosted proxy), `local` (sentence-transformers, default
  model `Qwen/Qwen3-Embedding-0.6B`); hybrid keyword+semantic via RRF (ADR-0004).
- **Incremental, persistent index** — startup syncs only changed session notes
  by `(mtime, size)` signature, so cost scales with new work, not history
  (ADR-0005).
- Benchmarks (offline, deterministic): `brief_reconstruction` (brief vs naive
  dump) and `supersession_benchmark` (supersession on vs off, in isolation). See
  `benchmarks/RESULTS.md`.
- **Supersede-by-best-match** — `log_event(supersedes_query=…)` retires the best-
  matching active event of the same type when the caller doesn't have its id;
  deterministic, auditable, opt-in (ADR-0007).
- **Tool annotations** — every tool now advertises `ToolAnnotations` to the host:
  `get_brief`/`search_memory` are read-only, `consolidate` is destructive (it
  archives sessions) and the only open-world tool (it may call an external LLM).
- `search_memory` results include each event's **id** (feed it straight into
  `log_event(supersedes=…)` to retire a stale decision you just found) and the
  tool exposes a `limit` parameter (default 10).
- **MCP registry manifest** (`server.json`) and a tag-driven **PyPI release
  workflow** using trusted publishing (`.github/workflows/release.yml`).
- Python **3.14** support (CI matrix, classifiers).

### Changed
- Retired the misleading `supersession_benchmark` head-to-head vs mem0 (keyword
  retrieval masked the mechanism, an empty answer scored as a win, and it wasn't
  reproducible offline); reframed the benchmarks honestly around the two above
  (ADR-0008).
- Pinned the MCP SDK to `mcp>=1.27,<2` — mcp 2.0 is a breaking rewrite
  (`FastMCP` → `MCPServer`); the v2 migration will be deliberate, not accidental.
- `log_event(type=…)` and `search_memory(scope=…)` are now `Literal`-typed, so
  the published schemas are enum-constrained: invalid values (e.g. a `'curent'`
  typo) are rejected by the protocol layer instead of being silently coerced.
- `__version__` is sourced from package metadata (`importlib.metadata`) instead
  of a hardcoded string, and the server no longer pokes the SDK's private
  `_mcp_server.version` attribute.

### Concurrency
- The shared SQLite connection (keyword index + semantic store) is now serialised
  by a reentrant lock, fixing interleaved-statement errors and lost writes when an
  MCP transport dispatches tool calls from worker threads.

### Security
- Validate `project` / `session_id` and sanitise entity names to prevent path
  traversal / arbitrary file writes outside the vault (covered by
  `tests/test_path_safety.py::test_safe_segment_rejects_traversal` and the
  engine-level `test_engine_rejects_unsafe_project`, which exercises the
  actual tool entry point).
- Strip ASCII control characters (except tab/newlines) from event content and
  link targets — `\x1f` is the index's packed-list separator and could corrupt
  the `links` column.

### Fixed
- Vault round-trip no longer loses or mis-attributes events whose content spans
  lines or contains `<!-- -->` / lookalike metadata.
- Cross-project supersession is resolved globally (briefs and search).
- Hardened SQLite (WAL + busy timeout) for concurrent sessions; atomic note
  writes; zero-vector handling consistent across the vec0 and brute-force paths.
- Index timestamps are normalised to UTC before storage: `ORDER BY created_at`
  compares ISO strings lexicographically, so mixed offsets (e.g. imported git
  history) could order the brief wrongly. `rebuild` self-heals existing indexes.
- `consolidate` archives sessions **before** appending distilled facts, so a
  crash between the two steps can no longer duplicate every fact on the next
  run (it merely loses regenerable facts — the sessions stay archived, intact).

## [0.1.0] — 2026-06-29

### Added
- Initial release: FastMCP server with `log_event`, `get_brief`, `search_memory`,
  `checkpoint`, `note_entity`; `session://brief` resource and `resume` prompt.
- Markdown vault as the source of truth with a derived SQLite + FTS5 index.
- Temporal supersession (retracted decisions excluded from briefs and recall).
- Deterministic, token-budgeted, graph-aware hand-off brief.
- Cross-project memory: project-scoped briefs, global `search_memory`.
- ADRs, architecture docs, two-session demo, and a Claude Code skill.
