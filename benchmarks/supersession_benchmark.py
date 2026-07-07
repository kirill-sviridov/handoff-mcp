"""Does temporal supersession actually retire stale decisions — in isolation?

This measures the core differentiator (temporal supersession) *on its own*,
offline and deterministically. It is deliberately NOT a head-to-head against
another memory store: an honest cross-system comparison needs both systems run
under the same retrieval and an LLM endpoint for the other store's extraction
step, which isn't reproducible in CI. (An earlier version compared against mem0
and was retired — see ADR-0008 — because its keyword retrieval let the "win" come
from query/term mismatches rather than from supersession, and an empty result
counted as a pass.)

Method — the same evolving-decisions scenario, retrieved two ways:

* **supersession ON**  — the events the brief actually shows (``active_events``);
* **supersession OFF** — the raw flat log, every event ever written.

Because retrieval is by *decision*, not by keyword, every stale fact IS reachable
in the OFF view — so any difference between ON and OFF is attributable to
supersession alone, not to a search miss. For each topic we check BOTH:

* the retracted decision is **absent** (no stale leak), and
* the current decision is **present** (so an empty answer can't score as a win).

Run:  python benchmarks/supersession_benchmark.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from handoff_mcp.models import Event, EventType
from handoff_mcp.supersession import active_events

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass
class Turn:
    """One statement in the evolving project history."""

    topic: str
    text: str
    # The earlier statement (by index) this one reverses, if any.
    retracts: int | None = None


# A realistic evolving-decisions scenario. Each `retracts` points at the index
# of the turn it overrides; the *stale* text must NOT survive as current.
SCENARIO: list[Turn] = [
    Turn("storage", "We store transactions in flat JSON files."),
    Turn("auth", "Authentication uses server-side session cookies."),
    Turn("deploy", "We deploy by copying files over SSH."),
    Turn("storage", "Switched storage to SQLite — we need aggregate queries.", retracts=0),
    Turn("queue", "Background jobs run in-process with a simple thread."),
    Turn("auth", "Moved auth to stateless JWT tokens, dropping cookies.", retracts=1),
    Turn("queue", "Replaced the in-process thread with a durable job queue.", retracts=4),
    Turn("deploy", "Now deploying via containers in CI, not SSH copy.", retracts=2),
]

# Per topic: (stale marker, current marker). A decision is a *stale leak* if it
# carries the stale marker but NOT the current one — i.e. it asserts the retracted
# choice as a standalone fact (a "switched from X to Y" line mentions both markers
# and is correctly not counted).
TOPIC_MARKERS: dict[str, tuple[str, str]] = {
    "storage": ("json", "sqlite"),
    "auth": ("cookie", "jwt"),
    "deploy": ("ssh", "container"),
    "queue": ("in-process", "durable"),
}
TOPICS = list(TOPIC_MARKERS)


@dataclass
class Result:
    mode: str
    stale_leaks: int
    current_present: int
    total_topics: int


def build_events() -> list[Event]:
    """Materialise the scenario as events with explicit supersedes edges."""

    ids = [f"ev_{i:03d}" for i in range(len(SCENARIO))]
    events: list[Event] = []
    for i, turn in enumerate(SCENARIO):
        supersedes = [ids[turn.retracts]] if turn.retracts is not None else []
        events.append(
            Event(
                id=ids[i],
                session_id="s_bench",
                project="bench",
                type=EventType.DECISION,
                content=turn.text,
                created_at=_T0 + timedelta(minutes=i),
                supersedes=supersedes,
            )
        )
    return events


def _has_stale(texts: list[str], stale: str, current: str) -> bool:
    return any(stale in t.lower() and current not in t.lower() for t in texts)


def _has_current(texts: list[str], current: str) -> bool:
    return any(current in t.lower() for t in texts)


def evaluate(events: list[Event], *, supersession: bool) -> Result:
    """Score the flat log (OFF) vs the active view (ON), by decision retrieval."""

    visible = active_events(events) if supersession else events
    texts_by_topic: dict[str, list[str]] = {t: [] for t in TOPICS}
    for ev, turn in zip(visible, _turns_for(visible, events), strict=True):
        texts_by_topic[turn.topic].append(ev.content)

    leaks = 0
    present = 0
    for topic in TOPICS:
        stale, current = TOPIC_MARKERS[topic]
        texts = texts_by_topic[topic]
        if _has_stale(texts, stale, current):
            leaks += 1
        if _has_current(texts, current):
            present += 1
    return Result(
        mode="supersession ON" if supersession else "supersession OFF",
        stale_leaks=leaks,
        current_present=present,
        total_topics=len(TOPICS),
    )


def _turns_for(visible: list[Event], all_events: list[Event]) -> list[Turn]:
    """Map each visible event back to its scenario Turn (for its topic)."""

    by_id = {ev.id: SCENARIO[i] for i, ev in enumerate(all_events)}
    return [by_id[ev.id] for ev in visible]


def main() -> list[Result]:
    events = build_events()
    print("Supersession-isolation benchmark (offline, deterministic)")
    print("=" * 70)
    print(
        f"Scenario: {len(SCENARIO)} decisions, {len(TOPICS)} topics, "
        f"{sum(1 for t in SCENARIO if t.retracts is not None)} retractions.\n"
    )

    results = [
        evaluate(events, supersession=False),
        evaluate(events, supersession=True),
    ]

    print(f"{'mode':<20}{'stale leaked':>14}{'current kept':>14}")
    print("-" * 70)
    for r in results:
        print(
            f"{r.mode:<20}{f'{r.stale_leaks}/{r.total_topics}':>14}"
            f"{f'{r.current_present}/{r.total_topics}':>14}"
        )
    print("-" * 70)
    print("Retrieval is by decision, so every stale fact is reachable in the OFF")
    print("view — the drop to 0 stale (while keeping all current decisions) is")
    print("supersession's doing, not a search miss. This is the mechanism in")
    print("isolation; see benchmarks/brief_reconstruction.py for the end-to-end")
    print("brief-vs-naive-dump comparison a resuming session actually experiences.")
    return results


if __name__ == "__main__":
    main()
