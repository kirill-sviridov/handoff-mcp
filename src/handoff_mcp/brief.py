"""Deterministic, token-budgeted hand-off brief.

The brief is the product a resuming session actually reads. It is built without
calling an LLM: given the active (non-superseded) events of a project, it sorts
them by *what a fresh session needs first*, then packs them into a token budget
in priority order — lower-priority items are the ones that get dropped when
space runs out.

Determinism is a feature, not a limitation — see
``docs/adr/0002-deterministic-brief.md``. The same vault state always yields the
same brief, which is what makes the reconstruction benchmark meaningful.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable

from .models import Brief, BriefSection, EntityRef, Event, EventType
from .supersession import active_events

# Max durable entities surfaced in a brief's "Related knowledge" section.
MAX_RELATED_ENTITIES = 4

# Section render order = priority order for a resuming session. A new session
# needs the goal and the next concrete step first; decisions and dead-ends stop
# it from undoing or repeating prior work; questions and files come last.
SECTION_ORDER: list[tuple[EventType, str]] = [
    (EventType.GOAL, "Goal"),
    (EventType.NEXT_STEP, "Next step"),
    (EventType.DECISION, "Decisions"),
    (EventType.DEADEND, "Dead ends (tried & failed)"),
    (EventType.QUESTION, "Open questions"),
    (EventType.FILE, "Files touched"),
]

# Rough tokens consumed by a bullet's markup/label beyond its content.
_EVENT_OVERHEAD = 6

# The brief shows a snippet of each event, not its full text, so it stays
# skimmable even when an agent logs an over-long, non-atomic event. The full
# text always remains in the vault (source of truth).
_SNIPPET_MAX_CHARS = 200


def estimate_tokens(text: str) -> int:
    """Cheap, model-agnostic token estimate (~4 chars/token)."""

    return max(1, math.ceil(len(text) / 4))


def snippet(content: str, max_chars: int = _SNIPPET_MAX_CHARS) -> str:
    """Trim event content to a skimmable one-liner, ending on a natural boundary."""

    content = " ".join(content.split())  # collapse whitespace to one line
    if len(content) <= max_chars:
        return content
    cut = content[:max_chars]
    for sep in (". ", "; ", " — ", ", ", " "):
        idx = cut.rfind(sep)
        if idx >= max_chars // 2:
            cut = cut[: idx + (len(sep) - 1 if sep != " " else 0)]
            break
    return cut.rstrip(" ,;—") + " …"


def _priority(event_type: EventType) -> int:
    for i, (etype, _) in enumerate(SECTION_ORDER):
        if etype == event_type:
            return i
    return len(SECTION_ORDER)


def _collect_related_entities(
    kept: list[Event],
    project: str,
    entity_summary: Callable[[str], str],
) -> list[EntityRef]:
    """Rank entities linked by the kept events by how often they're referenced."""

    counts: dict[str, int] = {}
    for ev in kept:
        for link in ev.links:
            counts[link] = counts.get(link, 0) + 1
    ranked = sorted(counts, key=lambda name: (-counts[name], name))
    return [
        EntityRef(name=name, project=project, snippet=entity_summary(name))
        for name in ranked[:MAX_RELATED_ENTITIES]
    ]


def build_brief(
    events: Iterable[Event],
    *,
    project: str,
    token_budget: int,
    entity_summary: Callable[[str], str] | None = None,
    retired_ids: set[str] | None = None,
    stale_session_ids: set[str] | None = None,
) -> Brief:
    """Assemble a prioritised, budget-bounded brief for ``project``.

    If ``entity_summary`` is given (a ``name -> one-line summary`` lookup), the
    brief is graph-aware: it appends the durable entity notes that the kept
    events link to, so a resuming session sees the relevant project knowledge.

    ``retired_ids`` is a precomputed set of superseded event ids (resolved
    globally across projects). When omitted, supersession is computed from the
    passed events alone — fine when ``events`` already spans every relevant event.

    ``stale_session_ids`` is the set of session ids whose sessions are finished
    (``status == "done"``). ``next_step`` events that are still active but come
    from one of these sessions are flagged via ``Brief.stale_event_ids`` — they
    survived (nothing newer superseded them) but the session that logged them is
    over, so they may no longer reflect the actual next step. When omitted
    (``None``), no events are marked.
    """

    materialised = list(events)
    if retired_ids is None:
        active_list = active_events(materialised)
    else:
        active_list = [ev for ev in materialised if ev.id not in retired_ids]
    active = [ev for ev in active_list if ev.project == project]

    # Global priority: section first, then importance, then recency.
    ranked = sorted(
        active,
        key=lambda e: (_priority(e.type), -e.importance, -e.created_at.timestamp()),
    )

    # Walk in priority order, packing what fits. An item too large for the
    # remaining budget is skipped (not a hard stop), so a smaller lower-priority
    # item can still be included after a large high-priority one is skipped — we
    # prefer filling the budget over leaving it idle. The budget governs event
    # content; section headings and the "Related knowledge" block are chrome on
    # top, so the rendered brief can be slightly larger than `token_budget`.
    kept: list[Event] = []
    budget_left = token_budget
    dropped = 0
    for ev in ranked:
        # Budget by the snippet that will actually be shown, so more atomic items
        # fit when events are verbose (the brief renders snippets, not full text).
        cost = estimate_tokens(snippet(ev.content)) + _EVENT_OVERHEAD
        if cost <= budget_left:
            kept.append(ev)
            budget_left -= cost
        else:
            dropped += 1

    # Regroup kept events into ordered sections for display.
    sections: list[BriefSection] = []
    for etype, title in SECTION_ORDER:
        members = [e for e in kept if e.type == etype]
        if not members:
            continue
        members.sort(key=lambda e: (-e.importance, -e.created_at.timestamp()))
        sections.append(BriefSection(title=title, events=members))

    related = (
        _collect_related_entities(kept, project, entity_summary)
        if entity_summary is not None
        else []
    )

    stale_ids = [
        ev.id
        for ev in kept
        if ev.type == EventType.NEXT_STEP
        and stale_session_ids is not None
        and ev.session_id in stale_session_ids
    ]

    brief = Brief(
        project=project,
        token_budget=token_budget,
        estimated_tokens=0,
        sections=sections,
        related_entities=related,
        dropped=dropped,
        stale_event_ids=stale_ids,
    )
    brief.estimated_tokens = estimate_tokens(render_brief(brief))
    return brief


def render_brief(brief: Brief) -> str:
    """Render a brief as compact markdown for an MCP resource / prompt."""

    lines: list[str] = [f"# Hand-off brief — {brief.project}", ""]
    if brief.is_empty():
        lines.append("_No prior session state recorded for this project yet._")
        return "\n".join(lines)

    for section in brief.sections:
        lines.append(f"## {section.title}")
        for ev in section.events:
            marker = " _(possibly stale)_" if ev.id in brief.stale_event_ids else ""
            lines.append(f"- {snippet(ev.content)}{marker}")
        lines.append("")

    if brief.related_entities:
        lines.append("## Related knowledge")
        for ent in brief.related_entities:
            suffix = f" — {ent.snippet}" if ent.snippet else ""
            lines.append(f"- [[{ent.name}]]{suffix}")
        lines.append("")

    if brief.dropped:
        lines.append(
            f"_({brief.dropped} lower-priority item(s) omitted to fit the "
            f"{brief.token_budget}-token budget.)_"
        )
    return "\n".join(lines).rstrip() + "\n"
