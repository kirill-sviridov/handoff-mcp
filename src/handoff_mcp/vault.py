"""Markdown vault — the source of truth.

The vault is a directory of plain markdown notes that a human can open directly
in Obsidian. Everything else in the system (the SQLite index, the brief) is
*derived* from these files and can be rebuilt from scratch at any time.

Layout::

    <vault>/
      <project>/
        sessions/<session_id>.md     # episodic: one note per Claude session
        entities/<Entity Name>.md    # durable: project knowledge (added later)

Session note format (human-readable, machine-parseable round-trip)::

    ---
    id: s_2026...
    project: agent-hub
    started_at: 2026-06-29T10:00:00+00:00
    status: active
    summary:
    ---

    # Session s_2026... — agent-hub

    ## Log

    - **[decision]** (imp:5 · <iso8601>) Use SQLite. <!-- id=ev_ab12 supersedes=ev_99 -->

Machine-only fields (``id``, ``supersedes``) live in a trailing HTML comment so
they are invisible in rendered markdown; ``type``/``importance``/``timestamp``
stay visible, and ``[[wiki-links]]`` are parsed back out of the content.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .models import Event, EventType, SessionMeta


def _atomic_write_text(path: Path, text: str) -> None:
    """Write a file atomically (temp file + os.replace) so a crash mid-write
    can never leave a truncated/corrupt note — the vault is the source of truth."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# - **[type]** (imp:N · ISO8601) content <!-- id=... supersedes=a,b -->
# `content` is greedy and the trailing meta comment is REQUIRED and anchored to
# start with `id=`: this makes the *last* `<!-- id=... -->` on the line the
# machine metadata, so content that itself contains `<!--`, `-->`, or a lookalike
# comment round-trips verbatim and cannot hijack the event id (content is kept
# single-line by the Event.content validator, so one bullet == one event).
_EVENT_RE = re.compile(
    r"^- \*\*\[(?P<type>\w+)\]\*\* "
    r"\(imp:(?P<imp>\d+) · (?P<ts>[^)]+)\) "
    r"(?P<content>.*) <!-- (?P<meta>id=.*?) -->\s*$"
)

_LOG_HEADING = "## Log"

# Filesystem safety. `project`/`session_id` become directory/file names, so they
# must never contain path separators, drive letters, or `..` — otherwise an
# (LLM-supplied) value could escape the vault and write arbitrary files.
_UNSAFE_FILE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def safe_segment(value: str, *, kind: str = "name") -> str:
    """Validate an identifier used as a path segment; raise on anything unsafe.

    Used for ``project`` and ``session_id`` — these are identifiers, so a bad
    value is an error (not silently mangled).
    """

    if not value or value in (".", ".."):
        raise ValueError(f"invalid {kind}: {value!r}")
    if _UNSAFE_FILE_CHARS.search(value) or "/" in value or "\\" in value:
        raise ValueError(f"invalid {kind}: {value!r} (path separators not allowed)")
    return value


def safe_filename(name: str) -> str:
    """Sanitise a free-text entity name into a safe single-path-component file stem.

    Entity names come from ``[[wiki-links]]`` and ``note_entity``, i.e. arbitrary
    text, so we sanitise rather than reject. Deterministic, so the same name
    always maps to the same file.
    """

    cleaned = _UNSAFE_FILE_CHARS.sub("_", name).strip().rstrip(". ")
    if not cleaned or set(cleaned) <= {"."}:
        return "_"
    if cleaned.split(".")[0].lower() in _WINDOWS_RESERVED:
        cleaned = "_" + cleaned
    return cleaned


def extract_links(content: str) -> list[str]:
    """Pull ``[[Entity]]`` targets out of free text, de-duplicated, in order."""

    seen: dict[str, None] = {}
    for m in _WIKILINK_RE.finditer(content):
        seen.setdefault(m.group(1).strip(), None)
    return list(seen)


def serialize_event(ev: Event) -> str:
    """Render one event as a markdown bullet line (no trailing newline)."""

    meta = [f"id={ev.id}"]
    if ev.supersedes:
        meta.append("supersedes=" + ",".join(ev.supersedes))
    comment = " <!-- " + " ".join(meta) + " -->"
    return (
        f"- **[{ev.type.value}]** (imp:{ev.importance} · "
        f"{ev.created_at.isoformat()}) {ev.content}{comment}"
    )


def parse_event_line(line: str, *, session_id: str, project: str) -> Event | None:
    """Parse a single log bullet back into an :class:`Event`, or ``None``."""

    m = _EVENT_RE.match(line.rstrip())
    if not m:
        return None
    try:
        etype = EventType(m.group("type"))
    except ValueError:
        return None

    meta_raw = m.group("meta") or ""
    fields: dict[str, str] = {}
    for token in meta_raw.split():
        if "=" in token:
            key, _, val = token.partition("=")
            fields[key] = val

    ev_id = fields.get("id")
    if not ev_id:
        return None
    supersedes = [s for s in fields.get("supersedes", "").split(",") if s]
    content = m.group("content")

    return Event(
        id=ev_id,
        session_id=session_id,
        project=project,
        type=etype,
        content=content,
        importance=int(m.group("imp")),
        created_at=datetime.fromisoformat(m.group("ts")),
        supersedes=supersedes,
        links=extract_links(content),
    )


def _serialize_frontmatter(meta: SessionMeta) -> str:
    return (
        "---\n"
        f"id: {meta.id}\n"
        f"project: {meta.project}\n"
        f"started_at: {meta.started_at.isoformat()}\n"
        f"status: {meta.status}\n"
        f"summary: {meta.summary or ''}\n"
        "---\n"
    )


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter dict, body). Tolerant of a missing block."""

    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end].strip("\n")
    body = text[end + 4 :].lstrip("\n")
    fm: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()
    return fm, body


class VaultStore:
    """Read/write access to the markdown vault."""

    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path
        # Guards session-note *creation*: with lazy creation, concurrent
        # first-ever log_event calls from different threads could otherwise
        # race to create the same file via ensure_session (each thinking it's
        # absent). Only creation is serialised — appends to an existing file
        # don't need this lock.
        self._create_lock = threading.Lock()

    # --- paths -----------------------------------------------------------
    def _sessions_dir(self, project: str) -> Path:
        return self.vault_path / safe_segment(project, kind="project") / "sessions"

    def _entities_dir(self, project: str) -> Path:
        return self.vault_path / safe_segment(project, kind="project") / "entities"

    def session_path(self, project: str, session_id: str) -> Path:
        return self._sessions_dir(project) / f"{safe_segment(session_id, kind='session_id')}.md"

    # --- sessions --------------------------------------------------------
    def ensure_session(self, meta: SessionMeta) -> Path:
        """Create the session note (with frontmatter + empty log) if absent."""

        path = self.session_path(meta.project, meta.id)
        if path.exists():
            return path
        with self._create_lock:
            if path.exists():  # a racing thread may have created it while we waited
                return path
            header = (
                _serialize_frontmatter(meta)
                + f"\n# Session {meta.id} — {meta.project}\n\n{_LOG_HEADING}\n\n"
            )
            _atomic_write_text(path, header)
        return path

    def append_event(self, event: Event) -> None:
        """Append an event bullet under the session's ``## Log`` heading."""

        path = self.session_path(event.project, event.session_id)
        if not path.exists():
            self.ensure_session(SessionMeta(id=event.session_id, project=event.project))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(serialize_event(event) + "\n")

    def read_session(self, project: str, session_id: str) -> tuple[SessionMeta, list[Event]]:
        path = self.session_path(project, session_id)
        text = path.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)
        meta = SessionMeta(
            id=fm.get("id", session_id),
            project=fm.get("project", project),
            started_at=datetime.fromisoformat(fm["started_at"])
            if fm.get("started_at")
            else datetime.now().astimezone(),
            status=fm.get("status", "active"),
            summary=fm.get("summary") or None,
        )
        events: list[Event] = []
        for line in body.splitlines():
            ev = parse_event_line(line, session_id=meta.id, project=meta.project)
            if ev is not None:
                events.append(ev)
        return meta, events

    def update_session_meta(self, meta: SessionMeta) -> None:
        """Rewrite only the frontmatter block, preserving the log body."""

        path = self.session_path(meta.project, meta.id)
        text = path.read_text(encoding="utf-8")
        _, body = _parse_frontmatter(text)
        _atomic_write_text(path, _serialize_frontmatter(meta) + "\n" + body)

    def archive_session(self, project: str, session_id: str) -> Path:
        """Move a session note out of the active set into ``<project>/archive/``.

        Used by consolidation: the originals are preserved (auditable, reversible)
        but no longer parsed/indexed, so the active vault and index shrink.
        """

        src = self.session_path(project, session_id)
        dest_dir = self.vault_path / safe_segment(project, kind="project") / "archive"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{safe_segment(session_id, kind='session_id')}.md"
        os.replace(src, dest)
        return dest

    # --- iteration -------------------------------------------------------
    def list_projects(self) -> list[str]:
        if not self.vault_path.exists():
            return []
        return sorted(
            p.name for p in self.vault_path.iterdir() if p.is_dir() and (p / "sessions").exists()
        )

    def iter_session_files(self, project: str | None = None) -> Iterator[Path]:
        projects = [project] if project else self.list_projects()
        for proj in projects:
            sdir = self._sessions_dir(proj)
            if sdir.exists():
                yield from sorted(sdir.glob("*.md"))

    def iter_events(self, project: str | None = None) -> Iterator[Event]:
        """Stream every event across the requested scope, vault order."""

        for path in self.iter_session_files(project):
            proj = path.parent.parent.name
            _, events = self.read_session(proj, path.stem)
            yield from events

    # --- durable project knowledge (entity notes) -----------------------
    def entity_path(self, project: str, name: str) -> Path:
        """Path of an entity note. The name is sanitised for the filesystem but
        kept human-readable so it round-trips as the ``[[wiki-link]]`` target."""

        return self._entities_dir(project) / f"{safe_filename(name)}.md"

    def ensure_entity(self, project: str, name: str) -> Path:
        """Create a stub entity note if it does not exist yet.

        Called whenever an event links to ``[[name]]`` so the knowledge graph is
        navigable in Obsidian even before the entity has hand-written content.
        """

        path = self.entity_path(project, name)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {name}\n\n", encoding="utf-8")
        return path

    def append_entity(self, project: str, name: str, content: str) -> Path:
        """Append a durable knowledge bullet to an entity note."""

        path = self.ensure_entity(project, name)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"- {content.strip()}\n")
        return path

    def read_entity(self, project: str, name: str) -> str:
        return self.entity_path(project, name).read_text(encoding="utf-8")

    def entity_summary(self, project: str, name: str) -> str:
        """First meaningful line of an entity note (skipping its heading).

        Returns "" if the entity is just a stub or does not exist.
        """

        path = self.entity_path(project, name)
        if not path.exists():
            return ""
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            return stripped[2:].strip() if stripped.startswith("- ") else stripped
        return ""

    def list_entities(self, project: str) -> list[str]:
        edir = self._entities_dir(project)
        if not edir.exists():
            return []
        return sorted(p.stem for p in edir.glob("*.md"))
