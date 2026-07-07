# ADR-0008: Retire the mem0 head-to-head; benchmark supersession honestly

- **Status:** Accepted
- **Date:** 2026-07-01

## Context

An earlier benchmark (`supersession_benchmark.py`) claimed a headline result:
**handoff-mcp 0/4 stale leaked vs mem0 4/4**. A close re-read of what it actually
measured showed the number was not honest:

1. **Retrieval was keyword (bm25), and the queries missed the stale facts
   regardless of supersession.** The topic query was the literal word `storage`,
   but the stale decision said "we **store** transactions in flat JSON" — no
   shared term, so it was never retrieved whether or not supersession ran. Proven
   directly: for 3 of the 4 topics, recall with supersession ON and OFF was
   *identical*. Only `deploy` exercised supersession at all.
2. **An empty result scored as a win.** For `deploy`, supersession-ON returned
   *nothing* (the query `deploy` didn't match "deploying" either), and zero
   returned memories trivially means zero stale leaks. A blank answer was counted
   as correctness.
3. **The comparison was structurally lopsided.** handoff-mcp is *told* which event
   supersedes which (its design — the agent logs `supersedes`); mem0 must *infer*
   retractions with an LLM step. The benchmark acknowledged this in prose but the
   `0/4 vs 4/4` headline did not.
4. **It was not reproducible offline.** mem0's extraction needs an OpenAI-
   compatible LLM endpoint + key, absent in CI and in the overnight environment,
   so the mem0 column could not be regenerated on demand.

## Decision

Retire the head-to-head. Do **not** ship a cross-system number we can't run
honestly and reproducibly. Instead:

- **`supersession_benchmark.py` now measures supersession in isolation** — the
  same evolving-decisions scenario, retrieved **by decision** (not by keyword) so
  every stale fact is reachable, compared **supersession ON vs OFF** within
  handoff-mcp. It scores two things per topic: stale-absent *and* current-present,
  so an empty answer can no longer pass. Result: OFF leaks 4/4 while keeping all
  current; ON drops to 0/4 while keeping all current. The delta is attributable to
  supersession alone.
- **`brief_reconstruction.py` is the primary, end-to-end benchmark** — the honest
  thing a resuming session actually experiences: the deterministic budgeted brief
  vs a naive full-context dump, on tokens, carried contradictions, and key-item
  coverage. Already offline and deterministic.
- **README + RESULTS.md** drop the `0/4 vs 4/4 vs mem0` table and are rewritten
  around these two offline benchmarks. mem0 is mentioned only as the *class* of
  similarity-store that has no supersession (with mem0's own docs marketing
  temporal reasoning as a paid tier), not as a benchmarked opponent with a
  fabricated score.

## Rationale

- **Honesty over loudness.** A quieter, true claim ("supersession removes exactly
  the retracted decisions; the brief stays bounded and contradiction-free while a
  naive dump doesn't") beats a louder one that inflates a keyword miss and an empty
  result into a 4-0 win.
- **Reproducibility.** Both surviving benchmarks run offline, deterministically,
  and are pinned by `tests/test_benchmark.py`, so the numbers can't silently rot.
- **Fair framing.** We compare mechanisms we can run on equal footing (supersession
  on vs off), and describe — rather than fake — the difference from similarity
  stores.

## Consequences

- We no longer publish a direct "beats mem0" number. If a fair, reproducible
  cross-system harness is built later (same retrieval for both, endpoint pinned,
  asymmetry stated), it can return — but as a separate, clearly-scoped artifact.
- The supersession benchmark is now a genuine regression guard for the mechanism,
  not a marketing figure.
