"""Supersede-by-best-match: retract a prior event without knowing its id.

An agent logging "we changed our mind about X" usually does NOT have the id of
the earlier decision to hand. ``log_event(supersedes_query=...)`` resolves the
single best-matching *active* event of the *same type* in the same project via
the deterministic keyword index and retires it — auditable (the resolved id is
persisted in the new event's ``supersedes``) and opt-in (the explicit-id path is
unchanged).
"""

from __future__ import annotations

from handoff_mcp.brief import render_brief
from handoff_mcp.engine import HandoffEngine
from handoff_mcp.models import EventType


def test_supersedes_query_retires_best_match(engine: HandoffEngine) -> None:
    old = engine.log_event(
        type=EventType.DECISION, content="Persist transactions in a flat JSON file."
    )
    new = engine.log_event(
        type=EventType.DECISION,
        content="Switch persistence to SQLite for aggregate queries.",
        supersedes_query="persist transactions storage",
    )
    assert old.id in new.supersedes
    body = render_brief(engine.get_brief())
    assert "SQLite" in body
    assert "JSON" not in body


def test_supersedes_query_only_retires_same_type(engine: HandoffEngine) -> None:
    # A goal and a decision share keywords; a decision's query must not retire the goal.
    goal = engine.log_event(type=EventType.GOAL, content="Ship the storage subsystem.")
    dec = engine.log_event(type=EventType.DECISION, content="Use a flat file for storage.")
    new = engine.log_event(
        type=EventType.DECISION,
        content="Use SQLite for storage instead.",
        supersedes_query="storage",
    )
    assert dec.id in new.supersedes
    assert goal.id not in new.supersedes
    body = render_brief(engine.get_brief())
    assert "Ship the storage subsystem." in body  # goal survives


def test_supersedes_query_no_match_is_noop(engine: HandoffEngine) -> None:
    new = engine.log_event(
        type=EventType.DECISION,
        content="A brand new decision.",
        supersedes_query="nonexistent unrelated topic xyzzy",
    )
    assert new.supersedes == []


def test_supersedes_query_skips_already_retired(engine: HandoffEngine) -> None:
    first = engine.log_event(type=EventType.DECISION, content="storage approach one alpha")
    second = engine.log_event(
        type=EventType.DECISION,
        content="storage approach two alpha",
        supersedes=[first.id],
    )
    third = engine.log_event(
        type=EventType.DECISION,
        content="storage approach three alpha",
        supersedes_query="storage approach alpha",
    )
    # `first` is already retired; the best *active* match is `second`.
    assert second.id in third.supersedes
    assert first.id not in third.supersedes


def test_supersedes_query_unions_with_explicit_ids(engine: HandoffEngine) -> None:
    a = engine.log_event(type=EventType.DECISION, content="explicit target unrelated foo")
    b = engine.log_event(type=EventType.DECISION, content="query target storage bar")
    new = engine.log_event(
        type=EventType.DECISION,
        content="supersede both at once",
        supersedes=[a.id],
        supersedes_query="storage bar",
    )
    assert a.id in new.supersedes  # explicit
    assert b.id in new.supersedes  # resolved by query
