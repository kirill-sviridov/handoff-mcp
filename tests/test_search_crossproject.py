"""Cross-project recall and scope behaviour through the engine."""

from __future__ import annotations

from handoff_mcp.brief import render_brief
from handoff_mcp.engine import HandoffEngine
from handoff_mcp.models import EventType


def _seed(engine: HandoffEngine) -> None:
    engine.log_event(
        type=EventType.DECISION, content="Adopt SQLite for the index", project="proj-a"
    )
    engine.log_event(
        type=EventType.DECISION,
        content="Solved long-job timeouts by chunking requests",
        project="hermes",
    )


def test_scope_all_finds_other_projects(engine: HandoffEngine) -> None:
    _seed(engine)
    hits = engine.search("timeout chunking", scope="all")
    assert any(h.event.project == "hermes" for h in hits)


def test_scope_current_stays_in_project(engine: HandoffEngine) -> None:
    _seed(engine)  # current project is proj-a
    hits = engine.search("timeout chunking", scope="current")
    assert all(h.event.project == "proj-a" for h in hits)


def test_search_excludes_superseded(engine: HandoffEngine) -> None:
    old = engine.log_event(type=EventType.DECISION, content="store as plain JSON file")
    engine.log_event(
        type=EventType.DECISION,
        content="store as SQLite database",
        supersedes=[old.id],
    )
    hits = engine.search("store", scope="current")
    contents = [h.event.content for h in hits]
    assert any("SQLite" in c for c in contents)
    assert not any("JSON" in c for c in contents)


def test_index_rebuilds_from_vault(engine: HandoffEngine) -> None:
    engine.log_event(type=EventType.GOAL, content="persisted goal")
    # Simulate a fresh process pointing at the same vault: index is rebuilt.
    reopened = HandoffEngine(engine.config)
    try:
        hits = reopened.search("persisted goal", scope="current")
        assert any("persisted goal" in h.event.content for h in hits)
    finally:
        reopened.close()


# --- cross-project supersession -------------------------------------------
def test_cross_project_supersession_excluded_from_brief(engine: HandoffEngine) -> None:
    old = engine.log_event(type=EventType.DECISION, content="old plan here", project="alpha")
    engine.log_event(
        type=EventType.DECISION, content="new plan here", supersedes=[old.id], project="beta"
    )
    body = render_brief(engine.get_brief(project="alpha"))
    assert "old plan" not in body


def test_cross_project_supersession_excluded_from_search(engine: HandoffEngine) -> None:
    old = engine.log_event(
        type=EventType.DECISION, content="zeta uniquephrase old", project="alpha"
    )
    engine.log_event(
        type=EventType.DECISION,
        content="zeta uniquephrase new",
        supersedes=[old.id],
        project="beta",
    )
    hits = engine.search("uniquephrase", scope="all")
    assert hits  # the new one is found
    assert all(h.event.id != old.id for h in hits)
