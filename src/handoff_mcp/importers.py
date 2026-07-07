"""Importers — bootstrap memory from data you already have.

So the tool is useful from minute one instead of an empty vault. These are pure
parsers (source text → events); the engine writes them via
:meth:`HandoffEngine.ingest_events`, which is idempotent (deterministic ids, so
re-importing doesn't duplicate).

* **git** — turn a repo's commit history into events: each commit is a decision
  (the subject) plus a files-touched note, timestamped at the commit date. Fully
  deterministic, no LLM.
* **Claude Code transcript** — best-effort: pull the first user prompt as a goal
  and file edits as file events from a session's JSONL. Tolerant of unknown shapes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .models import Event, EventType


def _stable_id(prefix: str, key: str) -> str:
    """Deterministic id from a key (stable across processes → idempotent import)."""

    return f"{prefix}{hashlib.blake2b(key.encode('utf-8'), digest_size=4).hexdigest()}"


# Cap files listed per commit so a sweeping change doesn't bloat one event.
_MAX_FILES_PER_COMMIT = 12
# Field/record separators used in the git log format we parse.
GIT_LOG_FORMAT = "__C__%x1f%H%x1f%aI%x1f%s"


def events_from_git_log(
    log_text: str, *, project: str, session_id: str = "s_import_git"
) -> list[Event]:
    """Parse ``git log --name-only --pretty=format:GIT_LOG_FORMAT`` output.

    Produces, per commit: a ``decision`` event (the subject) and, if files
    changed, one ``file`` event listing them. Ids derive from the commit hash so
    re-import is idempotent.
    """

    events: list[Event] = []
    current: dict[str, str] | None = None
    files: list[str] = []

    def flush() -> None:
        if current is None:
            return
        short = current["hash"][:8]
        created = _parse_iso(current["date"])
        subject = current["subject"].strip()
        if subject:
            events.append(
                Event(
                    id=f"ev_git_{short}",
                    session_id=session_id,
                    project=project,
                    type=EventType.DECISION,
                    content=f"Commit: {subject}",
                    importance=3,
                    created_at=created,
                )
            )
        if files:
            shown = files[:_MAX_FILES_PER_COMMIT]
            more = (
                ""
                if len(files) <= _MAX_FILES_PER_COMMIT
                else f" (+{len(files) - _MAX_FILES_PER_COMMIT} more)"
            )
            events.append(
                Event(
                    id=f"ev_git_{short}_f",
                    session_id=session_id,
                    project=project,
                    type=EventType.FILE,
                    content=f"Touched: {', '.join(shown)}{more}",
                    importance=1,
                    created_at=created,
                )
            )

    for line in log_text.splitlines():
        if line.startswith("__C__\x1f"):
            flush()
            _, hash_, date, subject = line.split("\x1f", 3)
            current = {"hash": hash_, "date": date, "subject": subject}
            files = []
        elif line.strip():
            files.append(line.strip())
    flush()
    return events


def events_from_claude_transcript(
    jsonl: str, *, project: str, session_id: str = "s_import_claude"
) -> list[Event]:
    """Best-effort import of a Claude Code transcript (JSONL).

    Extracts the first user prompt as a ``goal`` and file edits (Edit/Write/
    NotebookEdit tool calls) as ``file`` events. Tolerant: unparseable or
    unrecognised lines are skipped. Heuristic by design — prose decisions are not
    inferred here (that's what optional consolidation is for).
    """

    events: list[Event] = []
    seen_files: set[str] = set()
    goal_captured = False
    seq = 0

    for raw in jsonl.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        created = (
            _parse_iso(str(record.get("timestamp", ""))) if record.get("timestamp") else _epoch(seq)
        )
        seq += 1

        if not goal_captured:
            text = _first_user_text(record)
            if text:
                events.append(
                    Event(
                        id=f"ev_ct_{session_id}_goal",
                        session_id=session_id,
                        project=project,
                        type=EventType.GOAL,
                        content=text[:300],
                        importance=4,
                        created_at=created,
                    )
                )
                goal_captured = True

        for path in _edited_files(record):
            if path in seen_files:
                continue
            seen_files.add(path)
            events.append(
                Event(
                    id=_stable_id("ev_ct_", path),
                    session_id=session_id,
                    project=project,
                    type=EventType.FILE,
                    content=f"Edited {path}",
                    importance=1,
                    created_at=created,
                )
            )
    return events


# --- helpers -------------------------------------------------------------
def _parse_iso(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _epoch(0)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _epoch(offset: int) -> datetime:
    # Stable fallback timestamp for records without one (kept ordered by offset).
    return datetime(2000, 1, 1, tzinfo=timezone.utc).replace(microsecond=min(offset, 999_999))


def _content_blocks(record: dict[str, object]) -> list[dict[str, object]]:
    message = record.get("message", record)
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _first_user_text(record: dict[str, object]) -> str | None:
    message = record.get("message", record)
    role = message.get("role") if isinstance(message, dict) else record.get("role")
    if role != "user":
        return None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content.strip() or None
    for block in _content_blocks(record):
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            return str(block["text"]).strip() or None
    return None


def _edited_files(record: dict[str, object]) -> list[str]:
    paths: list[str] = []
    for block in _content_blocks(record):
        if block.get("type") == "tool_use" and block.get("name") in {
            "Edit",
            "Write",
            "NotebookEdit",
        }:
            tool_input = block.get("input")
            if isinstance(tool_input, dict):
                path = tool_input.get("file_path") or tool_input.get("notebook_path")
                if isinstance(path, str) and path:
                    paths.append(path)
    return paths
