"""Temporal supersession — the core differentiator.

A decision is never mutated in place. When the work supersedes an earlier
choice, the newer event records the id(s) it replaces via ``supersedes``. This
module computes which events are still *active* so the brief shows the current
state of the world rather than a flat, contradictory log.

The rule is intentionally simple and explainable: an event is **inactive** if
any other event lists it in ``supersedes``. Because every replacement edge is
explicit, chains resolve transitively for free — given A ← B ← C (C supersedes
B, B supersedes A), both A and B are inactive and only C survives.

This differs from embedding-similarity memory stores (mem0 / OpenMemory style),
which have no notion of one fact retiring another and so can surface stale,
contradicted facts. See ``benchmarks/supersession_benchmark.py`` for the
mechanism measured in isolation, and ADR-0008 for why we don't publish a
head-to-head score against those stores.
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import Event


def superseded_ids(events: Iterable[Event]) -> set[str]:
    """Ids that have been retired by some other event's ``supersedes`` edge."""

    retired: set[str] = set()
    for ev in events:
        # Ignore self-references so a malformed note cannot erase itself.
        retired.update(old for old in ev.supersedes if old != ev.id)
    return retired


def active_events(events: Iterable[Event]) -> list[Event]:
    """Return only the events that nothing else has superseded.

    Order is preserved from the input (which the vault yields chronologically).
    A self-reference (an event listing its own id) is ignored so a malformed
    note cannot erase itself.
    """

    materialised = list(events)
    retired = superseded_ids(materialised)
    return [ev for ev in materialised if ev.id not in retired]


def supersession_edges(events: Iterable[Event]) -> list[tuple[str, str]]:
    """(newer_id, older_id) edges — useful for tests, debugging, and diagrams."""

    edges: list[tuple[str, str]] = []
    for ev in events:
        for old in ev.supersedes:
            edges.append((ev.id, old))
    return edges
