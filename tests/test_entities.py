"""Durable project layer: entity notes and [[wiki-link]] graph linkage."""

from __future__ import annotations

from handoff_mcp.brief import render_brief
from handoff_mcp.engine import HandoffEngine
from handoff_mcp.models import EventType


def test_linking_an_event_creates_a_stub_entity(engine: HandoffEngine) -> None:
    engine.log_event(
        type=EventType.DECISION,
        content="Adopt a worker queue. See [[Architecture]].",
    )
    assert "Architecture" in engine.vault.list_entities(engine.config.project)
    text = engine.vault.read_entity(engine.config.project, "Architecture")
    assert text.startswith("# Architecture")


def test_brief_surfaces_related_entities(engine: HandoffEngine) -> None:
    engine.note_entity("Architecture", "Hub-and-gateway shell; agents via manifest.")
    engine.log_event(
        type=EventType.DECISION,
        content="Route all agent calls through the gateway. See [[Architecture]].",
    )
    brief = engine.get_brief()
    names = [e.name for e in brief.related_entities]
    assert "Architecture" in names
    arch = next(e for e in brief.related_entities if e.name == "Architecture")
    assert "Hub-and-gateway" in arch.snippet
    assert "## Related knowledge" in render_brief(brief)


def test_note_entity_appends_durable_knowledge(engine: HandoffEngine) -> None:
    engine.note_entity("Conventions", "All timestamps are stored in UTC.")
    engine.note_entity("Conventions", "Vault is the source of truth; index is derived.")
    text = engine.vault.read_entity(engine.config.project, "Conventions")
    assert "stored in UTC" in text
    assert "source of truth" in text


def test_entities_are_project_scoped(engine: HandoffEngine) -> None:
    engine.note_entity(
        "Architecture", "agent-hub uses a hub-and-gateway shell.", project="agent-hub"
    )
    assert "Architecture" in engine.vault.list_entities("agent-hub")
    # Not leaked into the current (different) project.
    assert "Architecture" not in engine.vault.list_entities(engine.config.project)


def test_entity_name_with_unsafe_chars_is_sanitised(engine: HandoffEngine) -> None:
    engine.note_entity("Component: API/Gateway", "Routes inbound calls.")
    entities = engine.vault.list_entities(engine.config.project)
    assert any("API" in e for e in entities)
    # Round-trips: we can read it back by the same name.
    assert "Routes inbound calls" in engine.vault.read_entity(
        engine.config.project, "Component: API/Gateway"
    )
