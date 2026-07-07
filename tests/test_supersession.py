"""Temporal supersession: retracted events drop out, transitively."""

from __future__ import annotations

from datetime import datetime, timezone

from handoff_mcp.models import Event, EventType
from handoff_mcp.supersession import active_events, superseded_ids, supersession_edges


def _ev(id_: str, supersedes: list[str] | None = None) -> Event:
    return Event(
        id=id_,
        session_id="s",
        project="p",
        type=EventType.DECISION,
        content=f"decision {id_}",
        created_at=datetime(2026, 6, 29, tzinfo=timezone.utc),
        supersedes=supersedes or [],
    )


def test_direct_supersession() -> None:
    a, b = _ev("a"), _ev("b", supersedes=["a"])
    active = active_events([a, b])
    assert [e.id for e in active] == ["b"]
    assert superseded_ids([a, b]) == {"a"}


def test_transitive_chain() -> None:
    # a <- b <- c : only c survives
    a, b, c = _ev("a"), _ev("b", ["a"]), _ev("c", ["b"])
    assert [e.id for e in active_events([a, b, c])] == ["c"]


def test_self_reference_does_not_erase() -> None:
    a = _ev("a", supersedes=["a"])
    assert [e.id for e in active_events([a])] == ["a"]


def test_edges_listed() -> None:
    a, b = _ev("a"), _ev("b", ["a"])
    assert supersession_edges([a, b]) == [("b", "a")]
