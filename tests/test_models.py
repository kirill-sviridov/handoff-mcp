"""Model validators: content/link sanitisation that protects storage layers."""

from __future__ import annotations

from pathlib import Path

from handoff_mcp.index import SqliteIndex
from handoff_mcp.models import Event, EventType


def _ev(content: str, links: list[str] | None = None) -> Event:
    return Event(
        id="ev_1",
        session_id="s",
        project="p",
        type=EventType.DECISION,
        content=content,
        links=links or [],
    )


# --- control characters are stripped from content ------------------------
def test_content_control_characters_are_stripped() -> None:
    # \x1f is the index's list-column separator; \x00/\x07 are plain garbage.
    ev = _ev("keep\ttab, drop\x1fthese\x00two\x07 chars")
    assert ev.content == "keep\ttab, dropthesetwo chars"


def test_content_newlines_still_collapse_to_spaces() -> None:
    ev = _ev("line one\nline two\r\nline three")
    assert ev.content == "line one line two line three"


def test_link_targets_are_sanitised() -> None:
    ev = _ev("x", links=["Foo\x1fBar", "Ok", "\x00"])
    assert ev.links == ["FooBar", "Ok"]  # separator stripped, empty target dropped


def test_unit_separator_in_links_cannot_corrupt_the_index(tmp_path: Path) -> None:
    """A \\x1f inside a link target must not split the packed links column."""

    idx = SqliteIndex(tmp_path / "x.db")
    try:
        idx.upsert_event(_ev("see the note", links=["Auth\x1fService", "Conventions"]))
        fetched = idx.get_event("ev_1")
        assert fetched is not None
        assert fetched.links == ["AuthService", "Conventions"]
    finally:
        idx.close()
