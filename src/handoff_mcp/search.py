"""Cross-project recall, with optional keyword / semantic / hybrid modes.

``get_brief`` is project-scoped (where did I leave off *here*), but search is
deliberately global by default. When the user says "in one of my projects we did
X", Claude should be able to call ``search_memory(scope='all')`` and pull the
relevant decision out of a *different* project's vault.

Three modes:

* ``keyword`` — FTS5 / bm25 lexical search (always available, the default).
* ``semantic`` — embedding cosine similarity (needs a :class:`SemanticStore`).
* ``hybrid``  — fuse the two ranked lists with Reciprocal Rank Fusion (RRF),
  which combines them robustly without having to reconcile bm25 and cosine
  score scales.

Superseded events are filtered out by default so recall reflects current state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from .index import SqliteIndex
from .models import SearchHit
from .semantic import SemanticStore

Scope = Literal["current", "all"]
Mode = Literal["keyword", "semantic", "hybrid"]

# RRF dampening constant; 60 is the value from the original Cormack et al. paper.
_RRF_K = 60

# Reranking blends relevance with recency (time-decay) and importance, so recall
# favours fresh, high-priority memories — all deterministic, no LLM.
_RECENCY_HALFLIFE_DAYS = 30.0
# Weights for (relevance, recency, importance); relevance still dominates.
_RERANK_WEIGHTS = (0.6, 0.25, 0.15)


def _project_filter(scope: Scope, current_project: str) -> list[str] | None:
    return [current_project] if scope == "current" else None


def search_memory(
    index: SqliteIndex,
    query: str,
    *,
    scope: Scope,
    current_project: str,
    limit: int = 10,
    include_superseded: bool = False,
    semantic: SemanticStore | None = None,
    mode: Mode = "keyword",
    rerank: bool = True,
    now: datetime | None = None,
) -> list[SearchHit]:
    """Recall over one or all projects using the requested ranking mode.

    When ``rerank`` is set (default), the merged hits are reordered by a
    deterministic blend of relevance, recency, and importance.
    """

    if mode in ("semantic", "hybrid") and semantic is None:
        # Gracefully degrade if semantic recall was requested but unavailable.
        mode = "keyword"

    projects = _project_filter(scope, current_project)
    over_fetch = limit * 3

    keyword_hits: list[SearchHit] = []
    if mode in ("keyword", "hybrid"):
        keyword_hits = index.search(query, projects=projects, limit=over_fetch)

    semantic_hits: list[SearchHit] = []
    if mode in ("semantic", "hybrid") and semantic is not None:
        candidate_ids: set[str] | None = None
        if projects is not None:
            candidate_ids = {e.id for e in index.events_for(current_project)}
        for event_id, score in semantic.search(
            query, candidate_ids=candidate_ids, limit=over_fetch
        ):
            event = index.get_event(event_id)
            if event is not None:
                semantic_hits.append(SearchHit(event=event, score=score, snippet=""))

    if mode == "keyword":
        merged = keyword_hits
    elif mode == "semantic":
        merged = semantic_hits
    else:
        merged = _rrf_fuse(keyword_hits, semantic_hits)

    if not include_superseded:
        merged = _drop_superseded(index, merged)
    if rerank:
        merged = _rerank(merged, now or datetime.now(tz=timezone.utc))
    return merged[:limit]


def _rerank(hits: list[SearchHit], now: datetime) -> list[SearchHit]:
    """Reorder by relevance + recency + importance (deterministic).

    Relevance is min-max normalised within the result set (scales differ by
    mode); recency is an exponential time-decay (``_RECENCY_HALFLIFE_DAYS``);
    importance maps 1..5 → 0..1. Ties break by event id for reproducibility.
    """

    if len(hits) <= 1:
        return hits
    rels = [h.score for h in hits]
    lo, hi = min(rels), max(rels)
    span = hi - lo
    w_rel, w_rec, w_imp = _RERANK_WEIGHTS

    def final_score(hit: SearchHit) -> float:
        rel = (hit.score - lo) / span if span else 0.5
        age_days = max((now - hit.event.created_at).total_seconds() / 86400.0, 0.0)
        recency = float(0.5 ** (age_days / _RECENCY_HALFLIFE_DAYS))
        importance = (hit.event.importance - 1) / 4.0
        return w_rel * rel + w_rec * recency + w_imp * importance

    ranked = sorted(hits, key=lambda h: (-final_score(h), h.event.id))
    return [h.model_copy(update={"score": final_score(h)}) for h in ranked]


def _rrf_fuse(a: list[SearchHit], b: list[SearchHit]) -> list[SearchHit]:
    """Reciprocal Rank Fusion of two ranked hit lists.

    Each list contributes ``1 / (k + rank)`` per event; scores are summed. This
    rewards events ranked highly by *either* signal without needing the two
    score scales to be comparable.
    """

    scores: dict[str, float] = {}
    hit_by_id: dict[str, SearchHit] = {}
    for ranked in (a, b):
        for rank, hit in enumerate(ranked):
            eid = hit.event.id
            scores[eid] = scores.get(eid, 0.0) + 1.0 / (_RRF_K + rank)
            hit_by_id.setdefault(eid, hit)

    # Sort by fused score desc, breaking ties by event id for deterministic,
    # reproducible ordering (independent of dict insertion order).
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [hit_by_id[eid].model_copy(update={"score": score}) for eid, score in ordered]


def _drop_superseded(index: SqliteIndex, hits: list[SearchHit]) -> list[SearchHit]:
    # Resolve supersession globally — a hit in project A may be retired by an
    # event in project B, so a per-project view would miss it.
    retired = index.all_superseded_ids()
    return [hit for hit in hits if hit.event.id not in retired]
