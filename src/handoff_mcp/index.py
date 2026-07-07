"""Derived SQLite + FTS5 index.

The vault is the source of truth; this index is a disposable accelerator built
*from* the vault. It exists for three things the brief and search need but that
are awkward over raw markdown:

* ranking (recency + importance) without re-reading every note,
* full-text search across projects (FTS5 / bm25),
* cheap temporal queries.

Anything here can be dropped and rebuilt with :meth:`SqliteIndex.rebuild`. See
``docs/adr/0001-sqlite-not-vectordb.md`` for why SQLite over a vector DB.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime, timezone
from pathlib import Path

from .models import Event, EventType, SearchHit

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    id          TEXT UNIQUE NOT NULL,
    session_id  TEXT NOT NULL,
    project     TEXT NOT NULL,
    type        TEXT NOT NULL,
    content     TEXT NOT NULL,
    importance  INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    supersedes  TEXT NOT NULL DEFAULT '',
    links       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project, created_at);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(project, session_id);

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    content,
    project UNINDEXED,
    event_id UNINDEXED,
    tokenize = 'unicode61'
);

-- Tracks the file signature of each session note already ingested, so startup
-- only re-parses sessions whose markdown changed (incremental sync at scale).
CREATE TABLE IF NOT EXISTS indexed_sessions (
    project     TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    signature   TEXT NOT NULL,
    PRIMARY KEY (project, session_id)
);
"""

_FTS_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)

# Separator for packing list columns (links/supersedes) into a TEXT cell. ASCII
# Unit Separator (0x1f) cannot occur in event content or wiki-link targets, so a
# link like [[Foo, Bar]] survives the round-trip (a comma would split it).
_LIST_SEP = "\x1f"


def _to_fts_query(query: str) -> str:
    """Turn arbitrary user text into a safe FTS5 MATCH expression.

    Each word becomes a quoted term (so FTS5 operators in user input can't break
    the query) and terms are OR-ed, which suits recall-style search better than
    the implicit-AND default.
    """

    tokens = _FTS_TOKEN_RE.findall(query)
    if not tokens:
        return '""'
    return " OR ".join(f'"{t}"' for t in tokens)


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        session_id=row["session_id"],
        project=row["project"],
        type=EventType(row["type"]),
        content=row["content"],
        importance=row["importance"],
        created_at=datetime.fromisoformat(row["created_at"]),
        supersedes=[s for s in row["supersedes"].split(_LIST_SEP) if s],
        links=[s for s in row["links"].split(_LIST_SEP) if s],
    )


class SqliteIndex:
    """Thin wrapper around a single SQLite connection."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)
        # check_same_thread=False lets the connection be used from a worker
        # thread, because an MCP transport may dispatch tool calls off the main
        # thread. The sqlite3 module does NOT make a connection safe for
        # *concurrent* use, though, and several operations here span multiple
        # statements (e.g. upsert_event = DELETE+INSERT+commit). A reentrant lock
        # serialises every access to this connection; the optional SemanticStore
        # shares the same connection and the same lock (see engine.py), so the
        # whole `.index.db` is guarded by one mutex. Reentrant so methods that
        # call other locked methods (e.g. rebuild -> upsert_event) don't deadlock.
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL + a busy timeout let concurrent sessions on one vault coexist
        # (a second writer waits rather than failing with "database is locked").
        if self.db_path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def __enter__(self) -> SqliteIndex:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- writes ----------------------------------------------------------
    def upsert_event(self, ev: Event) -> None:
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO events (id, session_id, project, type, content,
                                    importance, created_at, supersedes, links)
                VALUES (:id, :session_id, :project, :type, :content,
                        :importance, :created_at, :supersedes, :links)
                ON CONFLICT(id) DO UPDATE SET
                    content=excluded.content,
                    importance=excluded.importance,
                    supersedes=excluded.supersedes,
                    links=excluded.links
                """,
                {
                    "id": ev.id,
                    "session_id": ev.session_id,
                    "project": ev.project,
                    "type": ev.type.value,
                    "content": ev.content,
                    "importance": ev.importance,
                    # Normalise to UTC: created_at is compared lexicographically
                    # (ORDER BY created_at), and mixed offsets would sort wrongly
                    # ("…T12:00+05:00" > "…T10:00+00:00" as text, yet is earlier).
                    "created_at": ev.created_at.astimezone(timezone.utc).isoformat(),
                    "supersedes": _LIST_SEP.join(ev.supersedes),
                    "links": _LIST_SEP.join(ev.links),
                },
            )
            self.conn.execute("DELETE FROM events_fts WHERE event_id = ?", (ev.id,))
            self.conn.execute(
                "INSERT INTO events_fts (content, project, event_id) VALUES (?, ?, ?)",
                (ev.content, ev.project, ev.id),
            )
            self.conn.commit()

    def rebuild(self, events: Iterable[Event]) -> int:
        """Wipe and repopulate the index from an authoritative event stream."""

        with self.lock:
            self.conn.execute("DELETE FROM events")
            self.conn.execute("DELETE FROM events_fts")
            self.conn.execute("DELETE FROM indexed_sessions")
            count = 0
            for ev in events:
                self.upsert_event(ev)
                count += 1
            return count

    # --- incremental sync ------------------------------------------------
    def indexed_signature(self, project: str, session_id: str) -> str | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT signature FROM indexed_sessions WHERE project = ? AND session_id = ?",
                (project, session_id),
            ).fetchone()
        return row["signature"] if row else None

    def indexed_sessions(self) -> set[tuple[str, str]]:
        with self.lock:
            rows = self.conn.execute("SELECT project, session_id FROM indexed_sessions").fetchall()
        return {(r["project"], r["session_id"]) for r in rows}

    def delete_session(self, project: str, session_id: str) -> None:
        """Remove a session's events from the index (its note vanished/changed)."""

        with self.lock:
            ids = [
                r["id"]
                for r in self.conn.execute(
                    "SELECT id FROM events WHERE project = ? AND session_id = ?",
                    (project, session_id),
                ).fetchall()
            ]
            for event_id in ids:
                self.conn.execute("DELETE FROM events_fts WHERE event_id = ?", (event_id,))
            self.conn.execute(
                "DELETE FROM events WHERE project = ? AND session_id = ?", (project, session_id)
            )
            self.conn.execute(
                "DELETE FROM indexed_sessions WHERE project = ? AND session_id = ?",
                (project, session_id),
            )
            self.conn.commit()

    def replace_session_events(
        self, project: str, session_id: str, events: Iterable[Event], signature: str
    ) -> None:
        """Re-ingest one session's events and record its file signature."""

        with self.lock:
            self.delete_session(project, session_id)
            for ev in events:
                self.upsert_event(ev)
            self.conn.execute(
                "INSERT INTO indexed_sessions(project, session_id, signature) VALUES (?, ?, ?) "
                "ON CONFLICT(project, session_id) DO UPDATE SET signature = excluded.signature",
                (project, session_id, signature),
            )
            self.conn.commit()

    def iter_id_content(self) -> Iterator[tuple[str, str]]:
        """(id, content) for every indexed event — cheap feed for the embedder."""

        with self.lock:
            rows = self.conn.execute("SELECT id, content FROM events").fetchall()
        for row in rows:
            yield (row["id"], row["content"])

    # --- reads -----------------------------------------------------------
    def events_for(self, project: str) -> list[Event]:
        """All events for a project, oldest first (chronological)."""

        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE project = ? ORDER BY created_at, seq",
                (project,),
            ).fetchall()
        return [_row_to_event(r) for r in rows]

    def get_event(self, event_id: str) -> Event | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return _row_to_event(row) if row else None

    def all_superseded_ids(self) -> set[str]:
        """Ids retired by ANY event, across all projects.

        Supersession can cross project boundaries (an event in project B may
        retire one logged in project A), so the brief/search must resolve it
        globally rather than per-project. Self-references are ignored.
        """

        retired: set[str] = set()
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, supersedes FROM events WHERE supersedes != ''"
            ).fetchall()
        for row in rows:
            retired.update(s for s in row["supersedes"].split(_LIST_SEP) if s and s != row["id"])
        return retired

    def all_projects(self) -> list[str]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT DISTINCT project FROM events ORDER BY project"
            ).fetchall()
        return [r["project"] for r in rows]

    def search(
        self,
        query: str,
        *,
        projects: Sequence[str] | None = None,
        limit: int = 10,
    ) -> list[SearchHit]:
        """Full-text search, optionally restricted to ``projects``.

        Results are ordered by bm25 relevance (best first). bm25() returns lower
        values for better matches, so the exposed score is negated.
        """

        match = _to_fts_query(query)
        params: list[object] = [match]
        proj_clause = ""
        if projects:
            placeholders = ",".join("?" for _ in projects)
            proj_clause = f"AND e.project IN ({placeholders})"
            params.extend(projects)
        params.append(limit)

        sql = f"""
            SELECT e.*,
                   bm25(events_fts) AS rank,
                   snippet(events_fts, 0, '«', '»', '…', 12) AS snip
            FROM events_fts
            JOIN events e ON e.id = events_fts.event_id
            WHERE events_fts MATCH ? {proj_clause}
            ORDER BY rank
            LIMIT ?
        """
        hits: list[SearchHit] = []
        with self.lock:
            rows = self.conn.execute(sql, params).fetchall()
        for row in rows:
            hits.append(
                SearchHit(
                    event=_row_to_event(row),
                    score=-float(row["rank"]),
                    snippet=row["snip"] or "",
                )
            )
        return hits
