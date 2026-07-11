"""Silting fix: a new next_step retires active next_steps of PRIOR sessions."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from handoff_mcp.brief import render_brief
from handoff_mcp.config import HandoffConfig
from handoff_mcp.engine import HandoffEngine
from handoff_mcp.models import EventType


@pytest.fixture
def prior_engine(tmp_path: Path) -> Iterator[HandoffEngine]:
    cfg = HandoffConfig(vault_path=tmp_path / "vault", project="proj-a", session_id="s_prior_0001")
    eng = HandoffEngine(cfg)
    try:
        yield eng
    finally:
        eng.close()


@pytest.fixture
def current_engine(tmp_path: Path) -> Iterator[HandoffEngine]:
    cfg = HandoffConfig(
        vault_path=tmp_path / "vault", project="proj-a", session_id="s_current_0002"
    )
    eng = HandoffEngine(cfg)
    try:
        yield eng
    finally:
        eng.close()


def test_new_next_step_retires_prior_session_ones(
    prior_engine: HandoffEngine, current_engine: HandoffEngine
) -> None:
    old1 = prior_engine.log_event(type=EventType.NEXT_STEP, content="write the schema")
    old2 = prior_engine.log_event(type=EventType.NEXT_STEP, content="wire the ingest")
    prior_engine.checkpoint("done for today")

    current_engine.refresh_index()
    new = current_engine.log_event(type=EventType.NEXT_STEP, content="ship the API")

    assert set(new.supersedes) == {old1.id, old2.id}
    retired = current_engine.index.all_superseded_ids()
    assert old1.id in retired and old2.id in retired


def test_same_session_next_steps_do_not_retire_each_other(
    current_engine: HandoffEngine,
) -> None:
    first = current_engine.log_event(type=EventType.NEXT_STEP, content="step one")
    second = current_engine.log_event(type=EventType.NEXT_STEP, content="step two")
    assert first.id not in second.supersedes
    assert first.id not in current_engine.index.all_superseded_ids()


def test_other_types_and_other_projects_untouched(
    prior_engine: HandoffEngine, current_engine: HandoffEngine
) -> None:
    goal = prior_engine.log_event(type=EventType.GOAL, content="ship the agent")
    other = prior_engine.log_event(type=EventType.NEXT_STEP, content="unrelated", project="proj-b")
    prior_engine.checkpoint()

    current_engine.refresh_index()
    new = current_engine.log_event(type=EventType.NEXT_STEP, content="next")

    assert goal.id not in new.supersedes
    assert other.id not in new.supersedes


def test_manual_supersedes_preserved_without_duplicates(
    prior_engine: HandoffEngine, current_engine: HandoffEngine
) -> None:
    old = prior_engine.log_event(type=EventType.NEXT_STEP, content="old step")
    prior_engine.checkpoint()

    current_engine.refresh_index()
    new = current_engine.log_event(
        type=EventType.NEXT_STEP, content="new step", supersedes=[old.id]
    )
    assert new.supersedes.count(old.id) == 1


def test_already_retired_not_retired_again(
    prior_engine: HandoffEngine, current_engine: HandoffEngine
) -> None:
    old = prior_engine.log_event(type=EventType.NEXT_STEP, content="oldest")
    newer = prior_engine.log_event(
        type=EventType.NEXT_STEP, content="replacement", supersedes=[old.id]
    )
    prior_engine.checkpoint()

    current_engine.refresh_index()
    new = current_engine.log_event(type=EventType.NEXT_STEP, content="newest")

    assert new.supersedes == [newer.id]  # old is already retired — not re-listed


def test_non_next_step_logging_retires_nothing(
    prior_engine: HandoffEngine, current_engine: HandoffEngine
) -> None:
    old = prior_engine.log_event(type=EventType.NEXT_STEP, content="still valid")
    prior_engine.checkpoint()

    current_engine.refresh_index()
    current_engine.log_event(type=EventType.DECISION, content="chose sqlite")

    assert old.id not in current_engine.index.all_superseded_ids()


def test_brief_marks_done_session_next_step_possibly_stale(
    prior_engine: HandoffEngine, current_engine: HandoffEngine
) -> None:
    prior_engine.log_event(type=EventType.NEXT_STEP, content="finish the parser")
    prior_engine.checkpoint()  # session done; nothing newer logged since

    current_engine.refresh_index()
    text = render_brief(current_engine.get_brief())
    assert "finish the parser" in text
    assert "(possibly stale)" in text


def test_brief_does_not_mark_current_session_next_step(
    current_engine: HandoffEngine,
) -> None:
    current_engine.log_event(type=EventType.NEXT_STEP, content="fresh step")
    text = render_brief(current_engine.get_brief())
    assert "fresh step" in text
    assert "(possibly stale)" not in text


def test_brief_never_marks_other_event_types(
    prior_engine: HandoffEngine, current_engine: HandoffEngine
) -> None:
    prior_engine.log_event(type=EventType.DECISION, content="sqlite over json")
    prior_engine.checkpoint()

    current_engine.refresh_index()
    text = render_brief(current_engine.get_brief())
    assert "sqlite over json" in text
    assert "(possibly stale)" not in text
