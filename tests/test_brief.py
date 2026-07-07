"""Deterministic brief: ordering, supersession, and token budgeting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from handoff_mcp.brief import SECTION_ORDER, build_brief, estimate_tokens, render_brief
from handoff_mcp.models import Event, EventType
from handoff_mcp.vault import extract_links

_T0 = datetime(2026, 6, 29, tzinfo=timezone.utc)


def _ev(
    id_: str,
    type_: EventType,
    content: str,
    *,
    importance: int = 3,
    minute: int = 0,
    supersedes: list[str] | None = None,
) -> Event:
    return Event(
        id=id_,
        session_id="s",
        project="p",
        type=type_,
        content=content,
        importance=importance,
        created_at=_T0 + timedelta(minutes=minute),
        supersedes=supersedes or [],
        links=extract_links(content),
    )


def test_sections_follow_priority_order() -> None:
    events = [
        _ev("f", EventType.FILE, "main.py"),
        _ev("g", EventType.GOAL, "ship it"),
        _ev("n", EventType.NEXT_STEP, "write schema"),
    ]
    brief = build_brief(events, project="p", token_budget=1000)
    titles = [s.title for s in brief.sections]
    # Goal before Next step before Files, matching SECTION_ORDER.
    order_titles = [t for _, t in SECTION_ORDER]
    assert titles == [t for t in order_titles if t in titles]
    assert titles[0] == "Goal"


def test_superseded_excluded_from_brief() -> None:
    events = [
        _ev("d1", EventType.DECISION, "use JSON", minute=0),
        _ev("d2", EventType.DECISION, "use SQLite", minute=1, supersedes=["d1"]),
    ]
    body = render_brief(build_brief(events, project="p", token_budget=1000))
    assert "SQLite" in body
    assert "JSON" not in body


def test_other_projects_excluded() -> None:
    here = _ev("a", EventType.GOAL, "mine")
    other = Event(
        id="b",
        session_id="s",
        project="other",
        type=EventType.GOAL,
        content="theirs",
        created_at=_T0,
    )
    brief = build_brief([here, other], project="p", token_budget=1000)
    body = render_brief(brief)
    assert "mine" in body and "theirs" not in body


def test_budget_drops_lowest_priority_first() -> None:
    events = [
        _ev("g", EventType.GOAL, "G" * 80, importance=5),
        _ev("n", EventType.NEXT_STEP, "N" * 80, importance=5),
        _ev("f", EventType.FILE, "F" * 80, importance=1),
    ]
    # Budget large enough for ~2 of the 3 items.
    brief = build_brief(events, project="p", token_budget=50)
    body = render_brief(brief)
    assert brief.dropped >= 1
    # The low-importance FILE is the first to go; the GOAL stays.
    assert "G" * 80 in body
    assert "F" * 80 not in body
    # Kept event content fits the budget (chrome like headings is extra).
    kept_cost = sum(estimate_tokens(ev.content) + 6 for s in brief.sections for ev in s.events)
    assert kept_cost <= brief.token_budget


def test_budget_zero_drops_everything() -> None:
    events = [_ev("g", EventType.GOAL, "ship it", importance=5)]
    brief = build_brief(events, project="p", token_budget=0)
    assert brief.is_empty()
    assert brief.dropped == 1


def test_dropped_counter_is_exact() -> None:
    # Three equal-cost items; budget fits exactly one.
    events = [
        _ev("a", EventType.DECISION, "x" * 40, importance=5),
        _ev("b", EventType.DECISION, "y" * 40, importance=4),
        _ev("c", EventType.DECISION, "z" * 40, importance=3),
    ]
    cost_one = estimate_tokens("x" * 40) + 6
    brief = build_brief(events, project="p", token_budget=cost_one)
    assert sum(len(s.events) for s in brief.sections) == 1
    assert brief.dropped == 2


def test_within_section_sorted_by_importance() -> None:
    events = [
        _ev("d1", EventType.DECISION, "low", importance=2, minute=5),
        _ev("d2", EventType.DECISION, "high", importance=5, minute=0),
    ]
    brief = build_brief(events, project="p", token_budget=1000)
    decisions = next(s for s in brief.sections if s.title == "Decisions")
    assert [e.content for e in decisions.events] == ["high", "low"]


def test_related_entities_ranked_by_link_frequency() -> None:
    events = [
        _ev("d1", EventType.DECISION, "Use a worker. See [[Architecture]].", minute=0),
        _ev(
            "d2",
            EventType.DECISION,
            "Queue jobs. See [[Architecture]] and [[Conventions]].",
            minute=1,
        ),
    ]
    summaries = {"Architecture": "hub-and-gateway shell", "Conventions": "UTC everywhere"}
    brief = build_brief(
        events, project="p", token_budget=1000, entity_summary=lambda n: summaries.get(n, "")
    )
    names = [e.name for e in brief.related_entities]
    assert names == ["Architecture", "Conventions"]  # Architecture linked twice -> first
    body = render_brief(brief)
    assert "## Related knowledge" in body
    assert "[[Architecture]] — hub-and-gateway shell" in body


def test_no_related_section_without_lookup() -> None:
    events = [_ev("d1", EventType.DECISION, "Use a worker. See [[Architecture]].")]
    brief = build_brief(events, project="p", token_budget=1000)
    assert brief.related_entities == []
    assert "Related knowledge" not in render_brief(brief)


def test_long_event_is_snippeted_in_brief() -> None:
    long = "Chose SQLite over JSON for aggregate queries. " + "detail " * 80
    brief = build_brief([_ev("d", EventType.DECISION, long)], project="p", token_budget=1000)
    body = render_brief(brief)
    assert "…" in body  # truncated
    assert "Chose SQLite over JSON" in body  # kept the meaningful start
    assert len(body) < len(long)  # not the full blob


def test_short_event_is_not_snippeted() -> None:
    brief = build_brief(
        [_ev("d", EventType.DECISION, "Use SQLite.")], project="p", token_budget=1000
    )
    body = render_brief(brief)
    assert "Use SQLite." in body
    assert "…" not in body


def test_empty_brief_renders_placeholder() -> None:
    brief = build_brief([], project="p", token_budget=1000)
    assert brief.is_empty()
    assert "No prior session state" in render_brief(brief)
