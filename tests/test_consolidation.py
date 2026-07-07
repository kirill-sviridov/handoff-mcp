"""Memory consolidation: distil old sessions into entities, archive originals.

Uses a fake summariser (no LLM/network); the OpenAI backend is exercised via an
injected fake client mirroring the SDK shape.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from handoff_mcp.config import HandoffConfig
from handoff_mcp.engine import HandoffEngine
from handoff_mcp.models import Event, EventType
from handoff_mcp.summarizer import OpenAISummarizer


class FakeSummarizer:
    def __init__(self, facts: list[tuple[str, str]]) -> None:
        self.facts = facts
        self.seen: list[Event] = []

    def distill(self, events: list[Event]) -> list[tuple[str, str]]:
        self.seen = list(events)
        return self.facts


def _finished_session(config: HandoffConfig, session_id: str) -> list[Event]:
    """Create a separate, checkpointed (done) session and return its events."""

    eng = HandoffEngine(replace(config, session_id=session_id))
    evs = [
        eng.log_event(type=EventType.DECISION, content="Use a hub-and-gateway shell."),
        eng.log_event(type=EventType.DEADEND, content="Streaming times out; needs chunking."),
    ]
    eng.checkpoint(summary="done")
    eng.close()
    return evs


# --- candidate selection (deterministic, no LLM) ------------------------
def test_candidates_exclude_current_and_active_sessions(config: HandoffConfig) -> None:
    _finished_session(config, "s_old")
    engine = HandoffEngine(config)  # current session is config.session_id, active
    try:
        cands = engine.consolidation_candidates(config.project, older_than_days=None)
        assert "s_old" in cands
        assert config.session_id not in cands  # current/active session excluded
    finally:
        engine.close()


# --- consolidation flow --------------------------------------------------
def test_consolidate_distils_archives_and_reindexes(config: HandoffConfig) -> None:
    _finished_session(config, "s_old")
    engine = HandoffEngine(config)
    engine.summarizer = FakeSummarizer([("Architecture", "Hub-and-gateway shell.")])
    try:
        result = engine.consolidate(older_than_days=None)
        assert result.sessions_archived == 1
        assert result.facts_written == 1
        assert result.entities == ["Architecture"]

        # Durable knowledge written to the entity note.
        assert "Hub-and-gateway" in engine.vault.read_entity(config.project, "Architecture")
        # Original archived (out of sessions/, into archive/).
        assert not engine.vault.session_path(config.project, "s_old").exists()
        assert (config.vault_path / config.project / "archive" / "s_old.md").exists()
        # Events dropped from the active index.
        assert engine.index.events_for(config.project) == []
    finally:
        engine.close()


def test_consolidate_only_distils_active_events(config: HandoffConfig) -> None:
    # A session where a later decision supersedes an earlier one.
    eng = HandoffEngine(replace(config, session_id="s_old"))
    old = eng.log_event(type=EventType.DECISION, content="store as JSON")
    eng.log_event(type=EventType.DECISION, content="store as SQLite", supersedes=[old.id])
    eng.checkpoint()
    eng.close()

    engine = HandoffEngine(config)
    fake = FakeSummarizer([("Decisions", "Use SQLite.")])
    engine.summarizer = fake
    try:
        engine.consolidate(older_than_days=None)
        contents = [e.content for e in fake.seen]
        assert "store as SQLite" in contents
        assert "store as JSON" not in contents  # retracted decision not immortalised
    finally:
        engine.close()


def test_crash_before_facts_written_does_not_duplicate_on_rerun(
    config: HandoffConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sessions are archived BEFORE facts are appended, so a crash between the
    two steps loses the (regenerable) facts instead of writing them twice."""

    _finished_session(config, "s_old")
    engine = HandoffEngine(config)
    engine.summarizer = FakeSummarizer([("Architecture", "Hub-and-gateway shell.")])
    try:
        original_append = engine.vault.append_entity

        def boom(project: str, name: str, content: str) -> object:
            raise RuntimeError("crash mid-consolidation")

        monkeypatch.setattr(engine.vault, "append_entity", boom)
        with pytest.raises(RuntimeError):
            engine.consolidate(older_than_days=None)

        # The sessions were already archived, so a rerun finds no candidates —
        # nothing is distilled (or written) a second time.
        monkeypatch.setattr(engine.vault, "append_entity", original_append)
        result = engine.consolidate(older_than_days=None)
        assert result.sessions_archived == 0
        assert "nothing to consolidate" in result.note
        assert not engine.vault.entity_path(config.project, "Architecture").exists()
    finally:
        engine.close()


def test_consolidate_without_llm_is_a_noop(config: HandoffConfig) -> None:
    _finished_session(config, "s_old")
    engine = HandoffEngine(config)  # no llm_model configured → summarizer is None
    try:
        assert engine.summarizer is None
        result = engine.consolidate()
        assert result.sessions_archived == 0
        assert "no LLM" in result.note
        # The session was left untouched.
        assert engine.vault.session_path(config.project, "s_old").exists()
    finally:
        engine.close()


# --- OpenAI summariser backend (injected fake client) -------------------
def _fake_client(content: str) -> Any:
    chat = SimpleNamespace(
        completions=SimpleNamespace(
            create=lambda **kw: SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )
        )
    )
    return SimpleNamespace(chat=chat)


def _event(content: str) -> Event:
    return Event(id="e1", session_id="s", project="p", type=EventType.DECISION, content=content)


def test_openai_summarizer_parses_items() -> None:
    client = _fake_client('{"items": [{"entity": "Architecture", "fact": "Worker queue."}]}')
    summ = OpenAISummarizer("m", client=client)
    assert summ.distill([_event("x")]) == [("Architecture", "Worker queue.")]


def test_openai_summarizer_handles_bad_json() -> None:
    summ = OpenAISummarizer("m", client=_fake_client("not json at all"))
    assert summ.distill([_event("x")]) == []


def test_openai_summarizer_empty_events_skips_call() -> None:
    # No client call should be needed for an empty batch.
    summ = OpenAISummarizer("m", client=None)
    assert summ.distill([]) == []
