# Benchmark results

> Two offline, deterministic benchmarks, both reproducible from a clean checkout
> and pinned by `tests/test_benchmark.py`. No LLM, no network, no external store —
> so the numbers regenerate identically on any machine.
>
> An earlier revision published a head-to-head "0/4 vs mem0 4/4" figure. It was
> retired (see [ADR-0008](../docs/adr/0008-honest-benchmark.md)) because its
> keyword retrieval let the result come from query/term mismatches rather than
> from supersession, an empty answer counted as a pass, and it needed an LLM
> endpoint we can't run in CI. What follows is what we *can* stand behind.

## 1. Supersession in isolation

`python benchmarks/supersession_benchmark.py`

A project whose decisions evolve over 8 statements across 4 topics; each topic has
exactly one decision that a later one retracts (JSON→SQLite, cookies→JWT, SSH→
containers, in-process thread→durable queue). We retrieve the decisions **by type**
(not by keyword — so every stale fact is reachable) and compare the flat log
against the active view.

| mode | stale leaked | current kept |
|------|:------------:|:------------:|
| supersession OFF (flat log) | 4 / 4 | 4 / 4 |
| supersession ON (active view) | **0 / 4** | **4 / 4** |

- **stale leaked** — retracted decisions still asserted as current.
- **current kept** — the up-to-date decision is present. Scoring *both* is what
  stops an empty answer from passing as a "win": ON must retire every stale fact
  **and** retain every current one.
- Because retrieval is by decision, the stale facts are reachable in the OFF view,
  so the drop to `0/4` is supersession's doing, not a search miss. This isolates
  the mechanism; a similarity store with no notion of one fact retiring another
  behaves like the OFF row.

## 2. Brief vs naive full-context dump

`python benchmarks/brief_reconstruction.py`

What a resuming session actually reads: the deterministic, budgeted, supersession-
aware brief versus pasting back the whole event log. The brief is capped at 250
tokens; the dump is not. History grows left→right (extra low-value file events).

| history | naive tok | brief tok | reduction | key kept | naive contradictions | brief contradictions |
|--------:|----------:|----------:|----------:|:--------:|:--------------------:|:--------------------:|
| 28 ev | 363 | 217 | 1.7× | 6/6 | 3 | **0** |
| 48 ev | 633 | 217 | 2.9× | 6/6 | 3 | **0** |
| 108 ev | 1443 | 217 | 6.6× | 6/6 | 3 | **0** |
| 228 ev | 3063 | 217 | 14.1× | 6/6 | 3 | **0** |

- **contradictions** — retracted decisions still readable in the output. The naive
  dump carries every retraction forever; the brief carries none.
- **key kept** — fraction of active high-value items (decisions, dead-ends, next
  step) retained. The brief keeps them all while staying within budget.
- **reduction** — the naive dump grows without bound as history piles up; the brief
  stays flat (a soft cap — see below), so the gap widens with project age.

### Note on the token budget

The budget governs event **content**. Section headings and the "Related knowledge"
block are chrome added on top, so the rendered brief can sit slightly above the
configured number (here it settles at ~217 for a 250 budget; with a tighter budget
the chrome can push it a little over). It is a *soft* cap that keeps the brief
bounded and flat as history grows — not a hard byte limit.

## How to reproduce

```bash
uv pip install -e ".[dev]"
python benchmarks/supersession_benchmark.py
python benchmarks/brief_reconstruction.py
pytest tests/test_benchmark.py     # the invariants above, as regression guards
```

## On comparisons to similarity stores (mem0 / OpenMemory style)

We do not publish a benchmarked score against them, because a fair run needs both
systems under identical retrieval plus an LLM endpoint for the other store's
extraction step — not reproducible here, and easy to make misleading (as our own
retired benchmark showed). The *qualitative* difference is real and
uncontroversial: a flat similarity store has no notion of one fact retiring
another, so it behaves like the "supersession OFF" row above — as of this writing,
mem0's own docs market "temporal reasoning / memory decay" as a paid Platform
tier, i.e. the OSS store does not retire contradicted facts (unverified beyond
that public marketing claim; we have not audited mem0's OSS code for this).
handoff-mcp retires contradicted facts by construction, in the OSS package.
