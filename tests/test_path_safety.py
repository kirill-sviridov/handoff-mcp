"""Path traversal / unsafe-name defences for the vault's filesystem layout."""

from __future__ import annotations

import pytest

from handoff_mcp.engine import HandoffEngine
from handoff_mcp.models import EventType
from handoff_mcp.vault import safe_filename, safe_segment


@pytest.mark.parametrize("bad", ["../evil", "a/b", "a\\b", "..", ".", "C:/x", "x:y", ""])
def test_safe_segment_rejects_traversal(bad: str) -> None:
    with pytest.raises(ValueError):
        safe_segment(bad, kind="project")


def test_safe_segment_allows_normal_names() -> None:
    assert safe_segment("agent-hub", kind="project") == "agent-hub"


def test_safe_filename_neutralises_dangerous_names() -> None:
    assert safe_filename("..") == "_"
    assert safe_filename(".") == "_"
    assert safe_filename("a/b/c") == "a_b_c"
    assert safe_filename("CON").startswith("_")  # Windows reserved device name
    assert safe_filename("Auth ") == "Auth"  # trailing space stripped


def test_engine_rejects_unsafe_project(engine: HandoffEngine) -> None:
    with pytest.raises(ValueError):
        engine.log_event(type=EventType.GOAL, content="x", project="../../escape")
