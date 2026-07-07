"""Importers: git-log and Claude-transcript parsing + idempotent ingest."""

from __future__ import annotations

from datetime import datetime, timezone

from handoff_mcp.engine import HandoffEngine
from handoff_mcp.importers import events_from_claude_transcript, events_from_git_log
from handoff_mcp.models import EventType

US = "\x1f"

GIT_LOG = (
    f"__C__{US}abc123def456{US}2026-06-01T10:00:00+00:00{US}Add storage layer\n"
    "src/storage.py\n"
    "src/models.py\n"
    "\n"
    f"__C__{US}beef0000aaaa{US}2026-06-02T11:00:00+00:00{US}Fix the parser bug\n"
    "src/parser.py\n"
)


def test_git_log_parses_commits_and_files() -> None:
    events = events_from_git_log(GIT_LOG, project="proj")
    by_id = {e.id: e for e in events}

    assert by_id["ev_git_abc123de"].type == EventType.DECISION
    assert by_id["ev_git_abc123de"].content == "Commit: Add storage layer"
    assert by_id["ev_git_abc123de"].created_at == datetime(2026, 6, 1, 10, tzinfo=timezone.utc)
    assert by_id["ev_git_abc123de_f"].content == "Touched: src/storage.py, src/models.py"

    assert by_id["ev_git_beef0000"].content == "Commit: Fix the parser bug"
    assert by_id["ev_git_beef0000_f"].content == "Touched: src/parser.py"


def test_git_log_empty_input() -> None:
    assert events_from_git_log("", project="proj") == []


CLAUDE_JSONL = "\n".join(
    [
        '{"type":"user","timestamp":"2026-06-01T10:00:00Z",'
        '"message":{"role":"user","content":[{"type":"text","text":"Build the parser"}]}}',
        '{"type":"assistant","timestamp":"2026-06-01T10:01:00Z","message":{"role":"assistant",'
        '"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"src/parser.py"}}]}}',
        "not valid json — should be skipped",
        '{"type":"assistant","message":{"role":"assistant",'
        '"content":[{"type":"tool_use","name":"Write","input":{"file_path":"src/cli.py"}}]}}',
    ]
)


def test_claude_transcript_extracts_goal_and_files() -> None:
    events = events_from_claude_transcript(CLAUDE_JSONL, project="proj")
    goals = [e for e in events if e.type == EventType.GOAL]
    files = [e.content for e in events if e.type == EventType.FILE]

    assert len(goals) == 1
    assert goals[0].content == "Build the parser"
    assert "Edited src/parser.py" in files
    assert "Edited src/cli.py" in files


def test_claude_transcript_tolerates_garbage() -> None:
    assert events_from_claude_transcript("garbage\n{bad", project="proj") == []


# --- idempotent ingest through the engine -------------------------------
def test_ingest_is_idempotent(engine: HandoffEngine) -> None:
    events = events_from_git_log(GIT_LOG, project="proj")
    first = engine.ingest_events(events)
    second = engine.ingest_events(events)  # same source again
    assert first == len(events)
    assert second == 0  # nothing new on re-import

    # Imported events are searchable.
    hits = engine.search("parser", scope="all")
    assert any("parser" in h.event.content for h in hits)


def test_ingest_picks_up_new_events_on_reimport(engine: HandoffEngine) -> None:
    engine.ingest_events(events_from_git_log(GIT_LOG, project="proj"))
    extended = GIT_LOG + (
        f"__C__{US}cafe1234{US}2026-06-03T09:00:00+00:00{US}Add tests\ntests/test_x.py\n"
    )
    added = engine.ingest_events(events_from_git_log(extended, project="proj"))
    assert added == 2  # the new commit's decision + file events only
