"""Deterministic reranking of recall: relevance + recency + importance."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from handoff_mcp.index import SqliteIndex
from handoff_mcp.models import Event, EventType, SearchHit
from handoff_mcp.search import _rerank, search_memory

_NOW = datetime(2026, 6, 30, tzinfo=timezone.utc)


def _hit(eid: str, *, days_old: float, importance: int = 3, score: float = 1.0) -> SearchHit:
    return SearchHit(
        event=Event(
            id=eid,
            session_id="s",
            project="p",
            type=EventType.DECISION,
            content="x",
            importance=importance,
            created_at=_NOW - timedelta(days=days_old),
        ),
        score=score,
    )


def test_rerank_prefers_recent_when_relevance_tied() -> None:
    out = _rerank([_hit("old", days_old=120), _hit("new", days_old=1)], _NOW)
    assert [h.event.id for h in out] == ["new", "old"]


def test_rerank_prefers_higher_importance_when_other_factors_tied() -> None:
    out = _rerank(
        [_hit("low", days_old=10, importance=2), _hit("high", days_old=10, importance=5)],
        _NOW,
    )
    assert out[0].event.id == "high"


def test_rerank_relevance_still_dominates() -> None:
    # A much more relevant but slightly older hit should still win.
    strong_old = _hit("strong", days_old=20, score=10.0)
    weak_new = _hit("weak", days_old=0, score=0.0)
    out = _rerank([weak_new, strong_old], _NOW)
    assert out[0].event.id == "strong"


def test_rerank_passthrough_for_trivial_inputs() -> None:
    assert _rerank([], _NOW) == []
    one = [_hit("a", days_old=1)]
    assert [h.event.id for h in _rerank(one, _NOW)] == ["a"]


def test_search_rerank_promotes_recent_over_equally_relevant(tmp_path: Path) -> None:
    idx = SqliteIndex(tmp_path / "x.db")
    try:
        for eid, days in [("old", 200), ("new", 1)]:
            idx.upsert_event(
                Event(
                    id=eid,
                    session_id="s",
                    project="p",
                    type=EventType.DECISION,
                    content="alpha shared phrase",  # identical text → tied bm25
                    created_at=_NOW - timedelta(days=days),
                )
            )
        reranked = search_memory(
            idx, "alpha shared phrase", scope="all", current_project="p", now=_NOW
        )
        assert reranked[0].event.id == "new"
        # With reranking off, both are still returned (order is pure relevance).
        plain = search_memory(
            idx, "alpha shared phrase", scope="all", current_project="p", rerank=False
        )
        assert {h.event.id for h in plain} == {"old", "new"}
    finally:
        idx.close()
