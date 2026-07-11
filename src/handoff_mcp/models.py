"""Pydantic schemas shared across the vault, index, and MCP layers.

These models are the contract between the markdown vault (source of truth) and
the derived SQLite index. Every field that round-trips through markdown must be
representable in a human-readable note.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator

_NEWLINE_RUN = re.compile(r"\s*[\r\n]+\s*")

# ASCII control characters except \t, \n, \r (newlines are collapsed separately).
# \x1f matters most: the index packs the links/supersedes list columns with it
# (see index.py), so a stray unit separator in content would corrupt a row.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _single_line(text: str) -> str:
    """Collapse newlines to single spaces so a value stays one markdown line."""

    return _NEWLINE_RUN.sub(" ", text).strip()


class EventType(str, Enum):
    """The kind of progress signal captured by :func:`log_event`.

    The set is deliberately small and opinionated: each type maps to a distinct
    section of the hand-off brief, so the brief can be assembled deterministically
    without an LLM having to classify free text.
    """

    GOAL = "goal"  # what this session is trying to achieve
    DECISION = "decision"  # a choice made, ideally with rationale
    DEADEND = "deadend"  # tried & failed — so the next session does not repeat it
    FILE = "file"  # a file that was touched / is relevant
    QUESTION = "question"  # an open question that still needs answering
    NEXT_STEP = "next_step"  # the concrete next action to take


# Importance is a coarse 1..5 priority used by the deterministic ranker.
MIN_IMPORTANCE = 1
MAX_IMPORTANCE = 5
DEFAULT_IMPORTANCE = 3


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class Event(BaseModel):
    """A single atomic progress signal within a session.

    Events are append-only. A decision is never edited in place; instead a newer
    event ``supersedes`` it (see :mod:`handoff_mcp.supersession`). This keeps the
    vault an honest, auditable log while still letting the brief show only the
    currently-active state.
    """

    id: str = Field(description="Stable unique id, e.g. 'ev_a1b2c3'.")
    session_id: str = Field(description="Session that produced this event.")
    project: str = Field(description="Project namespace this event belongs to.")
    type: EventType
    content: str = Field(min_length=1, description="Human-readable body of the event.")
    importance: int = Field(default=DEFAULT_IMPORTANCE, ge=MIN_IMPORTANCE, le=MAX_IMPORTANCE)
    created_at: datetime = Field(default_factory=_utcnow)
    supersedes: list[str] = Field(
        default_factory=list,
        description="Ids of earlier events this one renders obsolete.",
    )
    links: list[str] = Field(
        default_factory=list,
        description="Wiki-link targets ([[Entity]]) extracted from the content.",
    )

    @field_validator("created_at")
    @classmethod
    def _ensure_tz(cls, v: datetime) -> datetime:
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)

    @field_validator("content")
    @classmethod
    def _strip(cls, v: str) -> str:
        # Keep content on a single line so it serialises to exactly one bullet
        # (a stray newline would otherwise split/lose the event on reload), and
        # drop ASCII control characters that could corrupt the markdown line or
        # the index's \x1f-packed list columns.
        return _single_line(_CONTROL_CHARS.sub("", v))

    @field_validator("links")
    @classmethod
    def _clean_links(cls, v: list[str]) -> list[str]:
        # Link targets are packed into one TEXT cell separated by \x1f; a control
        # character inside a target would split or corrupt it on read-back.
        cleaned = (_single_line(_CONTROL_CHARS.sub("", link)) for link in v)
        return [c for c in cleaned if c]


class SessionMeta(BaseModel):
    """Frontmatter of an episodic session note."""

    id: str
    project: str
    started_at: datetime = Field(default_factory=_utcnow)
    status: str = Field(default="active", description="active | done")
    summary: str | None = None

    @field_validator("summary")
    @classmethod
    def _one_line(cls, v: str | None) -> str | None:
        # Frontmatter is one `summary: …` line; collapse any newlines.
        return None if v is None else _single_line(v)


class BriefSection(BaseModel):
    """One titled block of the hand-off brief (e.g. 'Decisions')."""

    title: str
    events: list[Event] = Field(default_factory=list)


class EntityRef(BaseModel):
    """A pointer to a durable entity note that the active work links to."""

    name: str
    project: str
    snippet: str = Field(default="", description="One-line summary from the entity note.")


class Brief(BaseModel):
    """The prioritised, token-budgeted hand-off a new session reads on start.

    A brief is NOT a context dump: superseded events are excluded, sections are
    ordered by importance to a resuming session, and low-priority items are
    dropped first when the token budget is tight.
    """

    project: str
    generated_at: datetime = Field(default_factory=_utcnow)
    token_budget: int
    estimated_tokens: int
    sections: list[BriefSection] = Field(default_factory=list)
    related_entities: list[EntityRef] = Field(
        default_factory=list,
        description="Durable entity notes the active work links to (graph-aware).",
    )
    dropped: int = Field(default=0, description="Active events omitted due to the budget.")
    stale_event_ids: list[str] = Field(
        default_factory=list,
        description=(
            "next_step events from finished sessions — still active (nothing "
            "newer replaced them) but rendered with a 'possibly stale' marker."
        ),
    )

    def is_empty(self) -> bool:
        return all(not s.events for s in self.sections)


class SearchHit(BaseModel):
    """A single result from :func:`search_memory`."""

    event: Event
    score: float = Field(
        description=(
            "Relevance (higher is better). Raw FTS5/bm25 score, or the blended "
            "recency+importance+supersession rerank score when reranking is applied."
        )
    )
    snippet: str = Field(default="", description="Highlighted match context.")
