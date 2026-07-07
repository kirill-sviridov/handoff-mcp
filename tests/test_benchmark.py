"""Lock in the benchmark's headline invariants so they can't silently regress."""

from __future__ import annotations

import sys
from pathlib import Path

# benchmarks/ is not a package; make it importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks"))

from brief_reconstruction import build_history, evaluate
from supersession_benchmark import build_events
from supersession_benchmark import evaluate as evaluate_supersession


def test_supersession_off_leaks_every_stale_but_keeps_current() -> None:
    r = evaluate_supersession(build_events(), supersession=False)
    # The flat log carries all retracted decisions...
    assert r.stale_leaks == r.total_topics
    # ...but this is not a search miss: every current decision is reachable too.
    assert r.current_present == r.total_topics


def test_supersession_on_drops_all_stale_and_keeps_all_current() -> None:
    r = evaluate_supersession(build_events(), supersession=True)
    # Supersession removes exactly the stale facts, keeping all current ones —
    # so the win can't come from an empty answer.
    assert r.stale_leaks == 0
    assert r.current_present == r.total_topics


def test_brief_is_bounded_by_budget() -> None:
    small = evaluate(build_history(noise=0), token_budget=250)
    large = evaluate(build_history(noise=200), token_budget=250)
    # Brief size does not grow with history; naive dump does.
    assert large.brief_tokens <= 250
    assert large.brief_tokens == small.brief_tokens
    assert large.naive_tokens > small.naive_tokens * 3


def test_brief_never_carries_contradictions() -> None:
    for noise in (0, 50, 200):
        r = evaluate(build_history(noise=noise), token_budget=250)
        assert r.brief_contradictions == 0
        assert r.naive_contradictions > 0  # the baseline does carry them


def test_all_active_key_items_survive_the_budget() -> None:
    r = evaluate(build_history(noise=200), token_budget=250)
    assert r.coverage == 1.0
