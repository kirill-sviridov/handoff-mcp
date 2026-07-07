"""Search mode plumbing: RRF fusion and graceful degradation to keyword."""

from __future__ import annotations

from pathlib import Path

from handoff_mcp.index import SqliteIndex
from handoff_mcp.models import Event, EventType, SearchHit
from handoff_mcp.search import _rrf_fuse, search_memory


def _hit(eid: str) -> SearchHit:
    return SearchHit(
        event=Event(id=eid, session_id="s", project="p", type=EventType.DECISION, content="x"),
        score=0.0,
    )


def test_rrf_rewards_events_ranked_by_both_signals() -> None:
    keyword = [_hit("x"), _hit("y")]
    semantic = [_hit("z"), _hit("x")]  # x appears in both lists
    fused = _rrf_fuse(keyword, semantic)
    assert fused[0].event.id == "x"  # rewarded by both → ranked first
    assert {h.event.id for h in fused} == {"x", "y", "z"}


def test_search_degrades_to_keyword_when_semantic_missing(tmp_path: Path) -> None:
    idx = SqliteIndex(tmp_path / "x.db")
    try:
        idx.upsert_event(
            Event(
                id="e1", session_id="s", project="p", type=EventType.DECISION, content="alpha beta"
            )
        )
        # mode='hybrid' but no semantic store → must fall back to keyword, not crash.
        hits = search_memory(
            idx, "alpha", scope="all", current_project="p", mode="hybrid", semantic=None
        )
        assert any(h.event.id == "e1" for h in hits)
    finally:
        idx.close()
