"""SQLite index behaviour: sync lifecycle, storage round-trips, time ordering."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from handoff_mcp.config import HandoffConfig
from handoff_mcp.engine import HandoffEngine
from handoff_mcp.index import SqliteIndex
from handoff_mcp.models import Event, EventType
from handoff_mcp.vault import VaultStore


def _ev(eid: str, created_at: datetime) -> Event:
    return Event(
        id=eid,
        session_id="s",
        project="p",
        type=EventType.DECISION,
        content=f"decision {eid}",
        created_at=created_at,
    )


# --- timestamps are normalised to UTC before storage ---------------------
def test_mixed_offset_timestamps_order_chronologically(tmp_path: Path) -> None:
    """created_at is compared lexicographically (ORDER BY created_at), so events
    must be stored in UTC: "…T12:00+05:00" sorts *after* "…T10:00+00:00" as text
    even though it is two hours earlier in real time."""

    idx = SqliteIndex(tmp_path / "x.db")
    try:
        later_utc = _ev("later", datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc))
        earlier_plus5 = _ev(  # 12:00+05:00 == 07:00 UTC — before later_utc
            "earlier", datetime(2026, 7, 1, 12, 0, tzinfo=timezone(timedelta(hours=5)))
        )
        idx.upsert_event(later_utc)
        idx.upsert_event(earlier_plus5)

        events = idx.events_for("p")
        assert [e.id for e in events] == ["earlier", "later"]
        # And the round-tripped instants are unchanged (just re-expressed in UTC).
        assert events[0].created_at == earlier_plus5.created_at
        assert events[0].created_at.utcoffset() == timedelta(0)
    finally:
        idx.close()


# --- a comma in a wiki-link target survives the index --------------------
def test_link_with_comma_survives_index_roundtrip(engine: HandoffEngine) -> None:
    ev = engine.log_event(type=EventType.DECISION, content="See [[Foo, Bar]] for context")
    assert ev.links == ["Foo, Bar"]
    fetched = engine.index.get_event(ev.id)
    assert fetched is not None
    assert fetched.links == ["Foo, Bar"]


# --- incremental index sync ----------------------------------------------
def test_index_signature_and_session_lifecycle(tmp_path: Path) -> None:
    idx = SqliteIndex(tmp_path / "x.db")
    try:
        assert idx.indexed_signature("p", "s") is None
        ev = Event(id="e1", session_id="s", project="p", type=EventType.GOAL, content="hi")
        idx.replace_session_events("p", "s", [ev], "sig-1")
        assert idx.indexed_signature("p", "s") == "sig-1"
        assert ("p", "s") in idx.indexed_sessions()
        assert idx.get_event("e1") is not None
        idx.delete_session("p", "s")
        assert idx.get_event("e1") is None
        assert idx.indexed_signature("p", "s") is None
    finally:
        idx.close()


def test_unchanged_session_not_reparsed_on_restart(
    config: HandoffConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Run 1 creates session A with an event.
    a = HandoffEngine(config)
    a.log_event(type=EventType.GOAL, content="alpha")
    a.close()
    # Run 2 syncs the index (signs A) and opens a different session.
    b = HandoffEngine(replace(config, session_id="s_run2"))
    b.close()
    # Run 3: spy on read_session — A is unchanged and signed, so it must be skipped.
    seen: list[str] = []
    original = VaultStore.read_session

    def spy(self: VaultStore, project: str, session_id: str) -> object:
        seen.append(session_id)
        return original(self, project, session_id)

    monkeypatch.setattr(VaultStore, "read_session", spy)
    c = HandoffEngine(replace(config, session_id="s_run3"))
    c.close()
    assert config.session_id not in seen  # session A was NOT re-parsed


# --- SQLite hardening -----------------------------------------------------
def test_index_enables_wal(tmp_path: Path) -> None:
    idx = SqliteIndex(tmp_path / "x.db")
    try:
        mode = idx.conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        idx.close()
