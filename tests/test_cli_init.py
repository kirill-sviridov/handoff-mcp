"""handoff-init: idempotent injection of the memory block into agent files."""

from __future__ import annotations

from pathlib import Path

from handoff_mcp.cli_init import MARKER, ensure_block


def test_creates_file_when_missing(tmp_path: Path) -> None:
    target = tmp_path / "CLAUDE.md"
    assert ensure_block(target) == "created"
    assert MARKER in target.read_text(encoding="utf-8")
    assert "get_brief" in target.read_text(encoding="utf-8")


def test_creates_nested_path(tmp_path: Path) -> None:
    target = tmp_path / ".cursor" / "rules" / "handoff.mdc"
    assert ensure_block(target) == "created"
    assert target.exists()


def test_appends_without_clobbering(tmp_path: Path) -> None:
    target = tmp_path / "CLAUDE.md"
    target.write_text("# My Project\n\nExisting rules here.\n", encoding="utf-8")
    assert ensure_block(target) == "appended"
    text = target.read_text(encoding="utf-8")
    assert "Existing rules here." in text  # original kept
    assert MARKER in text  # block added


def test_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "CLAUDE.md"
    ensure_block(target)
    first = target.read_text(encoding="utf-8")
    assert ensure_block(target) == "present"  # second run is a no-op
    assert target.read_text(encoding="utf-8") == first  # unchanged, no duplicate


def test_block_mentions_sync() -> None:
    from handoff_mcp.cli_init import HANDOFF_BLOCK

    assert "handoff-sync" in HANDOFF_BLOCK or "HANDOFF_AUTO_SYNC" in HANDOFF_BLOCK
