"""Session notes are created lazily and closed out by checkpoint.

A server process is spun up per client connection, so an eagerly-written
session note would litter the vault with empty files on every reconnect.
"""

from __future__ import annotations

from handoff_mcp.config import HandoffConfig
from handoff_mcp.engine import HandoffEngine
from handoff_mcp.models import EventType


def test_engine_startup_does_not_create_a_session_file(config: HandoffConfig) -> None:
    engine = HandoffEngine(config)
    try:
        path = engine.vault.session_path(config.project, config.session_id)
        assert not path.exists()  # nothing logged yet → nothing written
    finally:
        engine.close()


def test_log_event_creates_the_session_file(config: HandoffConfig) -> None:
    engine = HandoffEngine(config)
    try:
        engine.log_event(type=EventType.DECISION, content="use SQLite")
        path = engine.vault.session_path(config.project, config.session_id)
        assert path.exists()
    finally:
        engine.close()


def test_checkpoint_with_no_events_does_not_create_a_session_file(
    config: HandoffConfig,
) -> None:
    engine = HandoffEngine(config)
    try:
        engine.checkpoint(summary="closed without doing anything")
        path = engine.vault.session_path(config.project, config.session_id)
        assert not path.exists()  # an empty, never-logged session leaves no litter
    finally:
        engine.close()


def test_checkpoint_after_log_event_marks_the_session_done(config: HandoffConfig) -> None:
    engine = HandoffEngine(config)
    try:
        engine.log_event(type=EventType.DECISION, content="use SQLite")
        engine.checkpoint(summary="done")
        meta, _ = engine.vault.read_session(config.project, config.session_id)
        assert meta.status == "done"
        assert meta.summary == "done"
    finally:
        engine.close()
