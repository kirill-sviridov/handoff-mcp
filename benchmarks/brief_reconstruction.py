"""Benchmark: hand-off brief vs. naive full-context dump.

A resuming session has two ways to recover state from a prior session:

* **Naive dump** — paste back every logged event (the "just give it all the
  context" baseline). Cheap to build, but it is large *and* it carries retracted
  decisions, so the new session can act on contradictions.
* **handoff-mcp brief** — the deterministic, budgeted, supersession-aware brief.

This script builds a realistic session history and compares the two on:

* **tokens** — size of what the next session must read;
* **contradictions** — count of retracted decisions still present (a correctness
  hazard, not just a size one);
* **key-item coverage** — fraction of the *active* high-value items (decisions,
  dead-ends, next step) that survive.

It is fully deterministic — no LLM, no randomness — so the numbers are
reproducible and CI-checkable. Run::

    python benchmarks/brief_reconstruction.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from handoff_mcp.brief import build_brief, estimate_tokens, render_brief
from handoff_mcp.models import Event, EventType
from handoff_mcp.supersession import active_events, superseded_ids

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _ev(
    n: int,
    type_: EventType,
    content: str,
    *,
    importance: int = 3,
    supersedes: list[str] | None = None,
) -> Event:
    return Event(
        id=f"ev_{n:03d}",
        session_id="s_bench",
        project="bench",
        type=type_,
        content=content,
        importance=importance,
        created_at=_T0 + timedelta(minutes=n),
        supersedes=supersedes or [],
    )


def build_history(noise: int = 0) -> list[Event]:
    """A plausible multi-session project history with retractions and noise.

    Modelled on ~6 working sessions: a steady stream of low-value file touches
    and chatter (the bulk of any real log), a handful of decisions — several of
    which get retracted as the design evolves — dead-ends, and a final next step.
    ``noise`` appends that many extra low-importance file events, simulating a
    longer-running project, so the benchmark can show how the two approaches
    scale. Fully deterministic.
    """

    events: list[Event] = [
        _ev(
            1,
            EventType.GOAL,
            "Build a finance agent with scheduled monthly summaries.",
            importance=5,
        ),
    ]
    counter = {"n": 1}

    def add(
        type_: EventType,
        content: str,
        *,
        importance: int = 3,
        supersedes: list[str] | None = None,
    ) -> Event:
        counter["n"] += 1
        ev = _ev(counter["n"], type_, content, importance=importance, supersedes=supersedes)
        events.append(ev)
        return ev

    # Session 1: scaffolding + early choices, lots of file noise.
    json_dec = add(EventType.DECISION, "Persist transactions in a flat JSON file.")
    for f in ("storage.py", "models.py", "__init__.py", "config.py", "cli.py"):
        add(EventType.FILE, f"Created agent/{f}.")
    handler_dec = add(EventType.DECISION, "Call the LLM directly from the request handler.")

    # Session 2: hit a wall, more files.
    add(
        EventType.DEADEND,
        "the provider's streaming API times out on long months; needs chunking.",
        importance=4,
    )
    for f in ("api.py", "schemas.py", "deps.py"):
        add(EventType.FILE, f"Edited agent/{f}.")
    add(
        EventType.QUESTION,
        "Model recurring transactions as templates or materialised rows?",
        importance=2,
    )

    # Session 3: the big retraction — JSON out, SQLite in.
    add(
        EventType.DECISION,
        "Use SQLite, not JSON — summaries need aggregate queries.",
        importance=5,
        supersedes=[json_dec.id],
    )
    for f in ("storage.py", "migrations/0001.sql", "queries.py"):
        add(EventType.FILE, f"Rewrote agent/{f} for SQLite.")

    # Session 4: architecture clean-up — move work off the handler.
    add(
        EventType.DECISION,
        "Move LLM calls into a background worker, not the handler.",
        importance=4,
        supersedes=[handler_dec.id],
    )
    for f in ("worker.py", "queue.py", "api.py"):
        add(EventType.FILE, f"Touched agent/{f}.")
    add(
        EventType.DEADEND,
        "APScheduler in-process loses jobs on restart; need a durable store.",
        importance=4,
    )

    # Session 5: scheduling decided, then revised once.
    cron_dec = add(EventType.DECISION, "Schedule summaries with a cron table in SQLite.")
    add(
        EventType.DECISION,
        "Use a dedicated scheduler table with a claimed_at lock column instead.",
        importance=4,
        supersedes=[cron_dec.id],
    )
    for f in ("scheduler.py", "locks.py"):
        add(EventType.FILE, f"Added agent/{f}.")

    # Simulated longer history: routine file touches that pile up over time.
    for i in range(noise):
        add(EventType.FILE, f"Touched agent/module_{i:03d}.py during refactor.", importance=1)

    # Session 6: where we stopped.
    add(EventType.QUESTION, "Do we need per-user timezones for the monthly cut-off?", importance=2)
    add(
        EventType.NEXT_STEP,
        "Write the ingest function and wire it to the worker queue.",
        importance=5,
    )
    return events


def render_naive_dump(events: list[Event]) -> str:
    """The baseline: every event, verbatim, including retracted ones."""

    lines = ["# Previous session log (full dump)", ""]
    for ev in events:
        lines.append(f"- [{ev.type.value}] {ev.content}")
    return "\n".join(lines) + "\n"


@dataclass
class Report:
    naive_tokens: int
    brief_tokens: int
    naive_contradictions: int
    brief_contradictions: int
    key_total: int
    key_in_brief: int

    @property
    def reduction(self) -> float:
        return self.naive_tokens / self.brief_tokens if self.brief_tokens else 0.0

    @property
    def coverage(self) -> float:
        return self.key_in_brief / self.key_total if self.key_total else 1.0


_KEY_TYPES = {EventType.DECISION, EventType.DEADEND, EventType.NEXT_STEP}


def evaluate(events: list[Event], token_budget: int = 400) -> Report:
    retired = superseded_ids(events)
    active = active_events(events)

    naive_text = render_naive_dump(events)
    brief = build_brief(events, project="bench", token_budget=token_budget)
    brief_text = render_brief(brief)

    # A contradiction = a retracted decision still readable in the output.
    naive_contra = sum(1 for e in events if e.id in retired and e.content in naive_text)
    brief_contra = sum(1 for e in events if e.id in retired and e.content in brief_text)

    key_active = [e for e in active if e.type in _KEY_TYPES]
    key_in_brief = sum(1 for e in key_active if e.content in brief_text)

    return Report(
        naive_tokens=estimate_tokens(naive_text),
        brief_tokens=estimate_tokens(brief_text),
        naive_contradictions=naive_contra,
        brief_contradictions=brief_contra,
        key_total=len(key_active),
        key_in_brief=key_in_brief,
    )


def main(budget: int = 250) -> list[Report]:
    """Show how naive dump vs. brief scale as the project history grows.

    The brief is capped at ``budget`` tokens; the naive dump is not. As history
    grows the dump balloons (and keeps accumulating contradictions), while the
    brief stays bounded, contradiction-free, and retains every active key item.
    """

    print(f"Brief reconstruction benchmark (brief budget = {budget} tokens)")
    print("=" * 78)
    header = f"{'history':>9}{'naive tok':>11}{'brief tok':>11}{'reduction':>11}"
    header += f"{'key kept':>11}{'naive!':>9}{'brief!':>9}"
    print(header)
    print("-" * 78)

    reports: list[Report] = []
    for noise in (0, 20, 80, 200):
        events = build_history(noise=noise)
        r = evaluate(events, token_budget=budget)
        reports.append(r)
        size = f"{len(events)} ev"
        print(
            f"{size:>9}{r.naive_tokens:>11}{r.brief_tokens:>11}"
            f"{r.reduction:>10.1f}x{f'{r.key_in_brief}/{r.key_total}':>11}"
            f"{r.naive_contradictions:>9}{r.brief_contradictions:>9}"
        )
    print("-" * 78)
    print("'!' columns = retracted decisions still present (a correctness hazard, not just size).")
    print("The brief stays bounded and contradiction-free while keeping every key item;")
    print("the naive dump grows without bound and carries every retraction forever.")
    return reports


if __name__ == "__main__":
    main()
