"""MCP server (FastMCP) — the transport adapter over :class:`HandoffEngine`.

Tool *descriptions* are load-bearing here. handoff-mcp never pushes anything to
the model on its own; the model decides when to call these tools. So each
description is written to reliably trigger a call at the right moment — at
session start (``get_brief``), as work happens (``log_event``), when the user
references prior work (``search_memory``), and at session end (``checkpoint``).
"""

from __future__ import annotations

import contextlib
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from . import sync as sync_ops
from .brief import render_brief
from .config import HandoffConfig
from .engine import HandoffEngine
from .models import EventType
from .search import Scope

# Mirrors models.EventType; spelled out as a Literal so FastMCP publishes an
# enum-constrained schema — invalid values are rejected by the protocol layer
# and the model sees the legal set right in the tool schema.
EventTypeName = Literal["goal", "decision", "deadend", "file", "question", "next_step"]


def create_server(engine: HandoffEngine) -> FastMCP:
    """Build a FastMCP app bound to a given engine (injectable for tests)."""

    mcp = FastMCP(
        "handoff-mcp",
        instructions=(
            "Persistent cross-session memory for Claude. At the start of a session "
            "call get_brief to load where the last session left off. As you work, "
            "call log_event to record goals, decisions, dead-ends, and next steps — "
            "one atomic item per call (1-2 sentences with the why), NOT a whole-"
            "session summary. When the user refers to past or other-project work, "
            "call search_memory. At the end of a session, call checkpoint."
        ),
    )

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,  # append-only: never edits or deletes prior events
            idempotentHint=False,  # each call appends a new event
            openWorldHint=False,
        )
    )
    def log_event(
        type: Annotated[
            EventTypeName,
            Field(
                description=(
                    "One of: 'goal' (what this session is achieving), 'decision' "
                    "(a choice made — include the rationale), 'deadend' (something "
                    "tried that failed — so it isn't repeated), 'file' (a file "
                    "touched or relevant), 'question' (an open question), "
                    "'next_step' (the concrete next action)."
                )
            ),
        ],
        content: Annotated[
            str,
            Field(
                description=(
                    "One atomic item in 1-2 sentences (a decision + its why, a single "
                    "dead-end, one next step) — not a multi-paragraph session summary. "
                    "Reference durable notes inline as [[Entity]] to link the graph."
                )
            ),
        ],
        importance: Annotated[
            int,
            Field(
                description="Priority 1-5 (5 = critical). Higher items survive the brief budget.",
                ge=1,
                le=5,
            ),
        ] = 3,
        supersedes: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Ids of earlier events this one makes obsolete (e.g. a reversed "
                    "decision). Superseded events are excluded from future briefs."
                )
            ),
        ] = None,
        supersedes_query: Annotated[
            str | None,
            Field(
                description=(
                    "Retire a prior event you don't have the id for: the single "
                    "best-matching ACTIVE event of the SAME type in this project is "
                    "found by keyword search and superseded. Use when you change a "
                    "past decision but don't know its id. The retired id is reported "
                    "back and recorded, so it stays auditable."
                )
            ),
        ] = None,
        project: Annotated[
            str | None,
            Field(description="Project namespace; defaults to the current session's project."),
        ] = None,
    ) -> str:
        """Record a progress signal for the current work session.

        Call this proactively as work happens — every time you set or change the
        goal, make a non-trivial decision (and why), hit a dead-end worth not
        repeating, touch an important file, surface an open question, or decide
        the next step.

        Keep each event ATOMIC and concise: one item per call, 1-2 sentences. Log
        several small events rather than dumping a whole session summary into one —
        the brief is meant to stay skimmable. Reference durable notes inline as
        [[Entity]] so they link in the brief's "Related knowledge".

        This is how the next session inherits your context. Returns the new event
        id (use it later in `supersedes`). If `supersedes_query` retired a prior
        event, a second line names which id was retired (or notes that none
        matched), so the retraction is auditable.
        """

        event = engine.log_event(
            type=EventType(type),
            content=content,
            importance=importance,
            supersedes=supersedes,
            supersedes_query=supersedes_query,
            project=project,
        )
        if not supersedes_query:
            return event.id
        # Audit line: what the query actually retired (ids beyond the explicit ones).
        explicit = set(supersedes or [])
        resolved = [sid for sid in event.supersedes if sid not in explicit]
        if not resolved:
            return f"{event.id}\n(no active {type} matched {supersedes_query!r} to supersede)"
        notes = []
        for sid in resolved:
            retired = engine.index.get_event(sid)
            label = f"«{retired.content[:60]}»" if retired is not None else ""
            notes.append(f"{sid} {label}".rstrip())
        return f"{event.id}\nSuperseded by best match: {'; '.join(notes)}"

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    def get_brief(
        project: Annotated[
            str | None,
            Field(description="Project to summarise; defaults to the current project."),
        ] = None,
        token_budget: Annotated[
            int | None,
            Field(description="Max tokens for the brief; lowest-priority items drop first."),
        ] = None,
    ) -> str:
        """Load the prioritised hand-off brief for a project.

        Call this at the START of a session, before doing anything else, to learn
        where the previous session left off: the goal, the next step, key
        decisions and their rationale, dead-ends already ruled out, and open
        questions. Retracted decisions are omitted. This is a curated brief, not a
        raw context dump.
        """

        if engine.config.auto_sync:
            # Best-effort: auto-sync must never block or fail a session.
            with contextlib.suppress(Exception):
                sync_ops.pull(engine.config.vault_path)
                engine.refresh_index()
        brief = engine.get_brief(project=project, token_budget=token_budget)
        return render_brief(brief)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    def search_memory(
        query: Annotated[
            str, Field(description="What to look for, in natural language or keywords.")
        ],
        scope: Annotated[
            Scope,
            Field(description="'current' = this project only; 'all' = every project in memory."),
        ] = "all",
        limit: Annotated[
            int,
            Field(description="Maximum number of results to return.", ge=1, le=50),
        ] = 10,
    ) -> str:
        """Search memory across sessions and projects.

        Call this whenever the user references past work, a prior decision, or
        ANOTHER project — e.g. "in one of my projects we did X", "how did we solve
        Y before", "what did we decide about Z", "last time". Use scope='all'
        (the default) to recall across every project; scope='current' to stay in
        this one. Returns matching past events with their project, content, and
        id — the id can be fed straight into log_event's `supersedes` to retire
        a decision you just found to be outdated.
        """

        hits = engine.search(query, scope=scope, limit=limit)
        if not hits:
            return "No matching memory found."
        lines = [f"Found {len(hits)} result(s):", ""]
        for h in hits:
            lines.append(
                f"- [{h.event.project}] ({h.event.type.value}) {h.event.content} (id: {h.event.id})"
            )
        return "\n".join(lines)

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,  # re-checkpointing an already-done session is a no-op
            openWorldHint=False,
        )
    )
    def checkpoint(
        summary: Annotated[
            str | None,
            Field(description="Optional one-line human summary of what this session accomplished."),
        ] = None,
    ) -> str:
        """Close out the current session and produce its hand-off brief.

        Call this at the END of a session, or when the user signals they're
        stopping or switching context. It finalises the session note and returns
        the brief the next session will read.
        """

        brief = engine.checkpoint(summary=summary)
        if engine.config.auto_sync:
            # Best-effort: auto-sync must never block or fail a session.
            with contextlib.suppress(Exception):
                sync_ops.sync(engine.config.vault_path)
        return render_brief(brief)

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,  # appends to the entity note, never rewrites it
            idempotentHint=False,
            openWorldHint=False,
        )
    )
    def note_entity(
        name: Annotated[
            str,
            Field(
                description=(
                    "Entity name, e.g. 'Architecture', 'Conventions', 'Auth "
                    "Service', 'Open Questions'. Becomes a [[wiki-link]] target."
                )
            ),
        ],
        content: Annotated[
            str,
            Field(description="A durable fact about this entity to remember long-term."),
        ],
        project: Annotated[
            str | None,
            Field(description="Project namespace; defaults to the current project."),
        ] = None,
    ) -> str:
        """Record durable, long-lived project knowledge on an entity note.

        Use this (rather than log_event) for facts that outlive a single session:
        the system's architecture, coding conventions, what a component does, or
        standing open questions. These notes are linked from sessions via
        [[wiki-links]] and form the project's knowledge graph. Returns the entity
        name.
        """

        return engine.note_entity(name=name, content=content, project=project)

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,  # archives session notes out of the active vault
            idempotentHint=False,
            openWorldHint=True,  # the only tool that may call an external LLM
        )
    )
    def consolidate(
        project: Annotated[
            str | None,
            Field(description="Project to consolidate; defaults to the current project."),
        ] = None,
        older_than_days: Annotated[
            int | None,
            Field(description="Only fold sessions finished more than this many days ago."),
        ] = None,
    ) -> str:
        """Compress old finished sessions into durable entity notes ('sleep').

        Call this when a project's session log has grown large and you want to
        reclaim space while keeping the lasting knowledge: it distils old sessions
        into the durable entity notes and archives the originals. Requires a
        configured LLM (HANDOFF_LLM_MODEL); it is the only step that uses one.
        """

        result = engine.consolidate(project=project, older_than_days=older_than_days)
        if result.note:
            return f"Consolidation: {result.note}."
        return (
            f"Consolidated {result.sessions_archived} session(s) into "
            f"{result.facts_written} durable fact(s) across entities: "
            f"{', '.join(result.entities)}."
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,  # a repeat sync converges to the same state
            openWorldHint=True,  # talks to a git remote over the network
        )
    )
    def sync(
        remote_url: Annotated[
            str | None,
            Field(
                description=(
                    "Only for FIRST-TIME setup: a PRIVATE git repo URL to bind "
                    "this vault to (the user provides it; they can create one via "
                    "`gh repo create <name> --private`). Omit once configured — a "
                    "plain call pulls, commits, and pushes."
                )
            ),
        ] = None,
    ) -> str:
        """Sync this device's memory vault with its private git remote.

        Use when the user wants their memory on another device, or to push/pull
        now. If the vault isn't configured yet, this returns setup instructions —
        relay them and ask the user for a private repo URL, then call again with
        remote_url. Once configured, a bare call pulls remote work, commits local
        changes, and pushes. Safe to call repeatedly.
        """

        vault = engine.config.vault_path
        if remote_url is not None and not sync_ops.is_configured(vault):
            setup_result = sync_ops.setup(vault, remote_url)
            if setup_result.status != sync_ops.STATUS_SYNCED:
                return setup_result.message
        result = sync_ops.sync(vault)
        if result.status == sync_ops.STATUS_SYNCED:
            engine.refresh_index()  # surface any pulled work to subsequent reads
        return result.message

    @mcp.resource("session://brief")
    def brief_resource() -> str:
        """The current project's hand-off brief, as a readable MCP resource."""

        return render_brief(engine.get_brief())

    @mcp.prompt()
    def resume() -> str:
        """A ready-to-send prompt that primes a new session with the brief."""

        brief = render_brief(engine.get_brief())
        return (
            "Resume the previous session using this hand-off brief. Continue from "
            "the next step; do not redo settled decisions or repeat dead-ends.\n\n"
            f"{brief}"
        )

    return mcp


def main() -> None:
    """Console-script entry point: run the server over stdio."""

    engine = HandoffEngine(HandoffConfig())
    server = create_server(engine)
    server.run()


if __name__ == "__main__":
    main()
