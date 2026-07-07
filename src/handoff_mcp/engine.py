"""HandoffEngine — the application core, independent of the MCP transport.

The engine wires the vault (source of truth) to the SQLite index (accelerator)
and exposes the four high-level operations the MCP tools map onto: ``log_event``,
``checkpoint``, ``get_brief``, ``search_memory``.

Keeping this layer transport-free means the whole system is testable without
spinning up an MCP client, and the server module stays a thin adapter.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .brief import build_brief
from .config import HandoffConfig
from .embeddings import make_embedder
from .index import SqliteIndex
from .models import Brief, Event, EventType, SearchHit, SessionMeta
from .search import Mode, Scope, search_memory
from .semantic import SemanticStore
from .summarizer import Summarizer, make_summarizer
from .vault import VaultStore, extract_links


@dataclass
class ConsolidationResult:
    """Summary of a consolidation pass."""

    project: str
    sessions_archived: int
    facts_written: int
    entities: list[str]
    note: str = ""


def new_event_id() -> str:
    return f"ev_{secrets.token_hex(4)}"


class HandoffEngine:
    """Owns the vault + index for one running session."""

    def __init__(self, config: HandoffConfig) -> None:
        self.config = config
        config.ensure_dirs()
        self.vault = VaultStore(config.vault_path)
        self.index = SqliteIndex(config.db_path)
        # The index is derived from the vault (the source of truth) and persists
        # across runs. On startup we sync incrementally — only sessions whose
        # markdown changed are re-parsed — so startup cost scales with *new*
        # work, not total history.
        self._sync_index()

        # Optional semantic layer (off unless configured). Shares the index's
        # connection so vectors live in the same .index.db file. Fed from the
        # (already-synced) index rather than re-parsing the vault; embedding is
        # itself incremental (only new events are embedded).
        self.semantic: SemanticStore | None = None
        if config.enable_semantic:
            embedder = make_embedder(
                config.embedder,
                model=config.embed_model,
                embedding_dim=config.embedding_dim,
            )
            self.semantic = SemanticStore(self.index.conn, embedder, lock=self.index.lock)
            self.semantic.rebuild(self.index.iter_id_content())

        # Optional LLM summariser for consolidation (off unless a model is set).
        self.summarizer: Summarizer | None = make_summarizer(
            config.llm_model, base_url=config.llm_base_url, api_key=config.llm_api_key
        )

        # The session note is created lazily, on first log_event/checkpoint (see
        # below) — not here. A server process is spun up per client connection,
        # so writing it eagerly littered the vault with empty session files for
        # every connection that never logged anything (e.g. quick reconnects).

    def _sync_index(self) -> None:
        """Bring the derived index in line with the vault, incrementally.

        Each session note's (mtime, size) signature is compared to what the index
        last ingested; only changed/new notes are re-parsed, and notes that
        disappeared are dropped. A deleted ``.index.db`` self-heals (every note
        looks new). This keeps startup O(new events), not O(total history).
        """

        seen: set[tuple[str, str]] = set()
        for path in self.vault.iter_session_files():
            project = path.parent.parent.name
            session_id = path.stem
            seen.add((project, session_id))
            stat = path.stat()
            signature = f"{stat.st_mtime_ns}:{stat.st_size}"
            if self.index.indexed_signature(project, session_id) == signature:
                continue
            _, events = self.vault.read_session(project, session_id)
            self.index.replace_session_events(project, session_id, events, signature)

        for project, session_id in self.index.indexed_sessions() - seen:
            self.index.delete_session(project, session_id)

    def close(self) -> None:
        self.index.close()

    def refresh_index(self) -> None:
        """Re-sync the derived index from the vault after external changes (e.g.
        a git pull brought in new session files). Cheap: only changed notes are
        re-parsed. Rebuilds the semantic layer too when it is enabled."""

        self._sync_index()
        if self.semantic is not None:
            self.semantic.rebuild(self.index.iter_id_content())

    # --- operations ------------------------------------------------------
    def log_event(
        self,
        *,
        type: EventType,
        content: str,
        importance: int | None = None,
        supersedes: list[str] | None = None,
        supersedes_query: str | None = None,
        project: str | None = None,
    ) -> Event:
        """Record one progress signal in the current session.

        ``project`` defaults to the session's project, but may name another
        namespace so a single session can touch multiple projects.

        ``supersedes`` retires earlier events by *id*. ``supersedes_query`` is the
        ergonomic alternative for when the agent doesn't have that id: it retires
        the single best-matching *active* event of the *same type* in this event's
        project, resolved deterministically via the keyword index. The resolved id
        is unioned into ``supersedes`` and persisted, so the retraction is fully
        auditable; if nothing matches, nothing is retired.
        """

        proj = project or self.config.project
        resolved = list(supersedes or [])
        if supersedes_query:
            match = self._resolve_supersedes_query(supersedes_query, proj, type)
            if match is not None and match not in resolved:
                resolved.append(match)

        event = Event(
            id=new_event_id(),
            session_id=self.config.session_id,
            project=proj,
            type=type,
            content=content,
            importance=importance if importance is not None else 3,
            supersedes=resolved,
            links=extract_links(content),
        )
        self.vault.append_event(event)
        self.index.upsert_event(event)
        if self.semantic is not None:
            self.semantic.upsert(event.id, event.content)
        # Graph linkage: ensure a navigable entity note exists for every
        # [[wiki-link]] the event mentions (in that event's project namespace).
        for link in event.links:
            self.vault.ensure_entity(event.project, link)
        return event

    def _resolve_supersedes_query(self, query: str, project: str, type: EventType) -> str | None:
        """Pick the id of the single best-matching *active* event to retire.

        Deterministic: ranks ``project``'s events by bm25 relevance to ``query``
        (keyword index) and returns the first that is still active and of the same
        ``type``. Same-type only, so a new decision retires a prior *decision*, not
        an unrelated goal that happens to share words. Returns ``None`` when no
        candidate matches — the caller then retires nothing rather than guessing.
        """

        retired = self.index.all_superseded_ids()
        for hit in self.index.search(query, projects=[project], limit=10):
            if hit.event.id in retired:
                continue  # already superseded — find the live one
            if hit.event.type != type:
                continue  # retire like-for-like only
            return hit.event.id
        return None

    def note_entity(self, name: str, content: str, project: str | None = None) -> str:
        """Record durable project knowledge on an entity note (Architecture,
        Conventions, Component X, ...). Returns the entity name."""

        self.vault.append_entity(project or self.config.project, name, content)
        return name

    def ingest_events(self, events: Iterable[Event]) -> int:
        """Write pre-built events (e.g. from an importer) into memory, idempotently.

        Events are grouped by their (project, session) and only ids not already
        present in that session note are written — so re-importing the same
        source is a no-op. Returns the number of newly written events.
        """

        groups: dict[tuple[str, str], list[Event]] = {}
        for ev in events:
            groups.setdefault((ev.project, ev.session_id), []).append(ev)

        imported = 0
        for (project, session_id), batch in groups.items():
            self.vault.ensure_session(SessionMeta(id=session_id, project=project))
            _, existing = self.vault.read_session(project, session_id)
            existing_ids = {e.id for e in existing}
            for ev in batch:
                if ev.id in existing_ids:
                    continue
                self.vault.append_event(ev)
                self.index.upsert_event(ev)
                if self.semantic is not None:
                    self.semantic.upsert(ev.id, ev.content)
                for link in ev.links:
                    self.vault.ensure_entity(ev.project, link)
                existing_ids.add(ev.id)
                imported += 1
        return imported

    def checkpoint(self, summary: str | None = None) -> Brief:
        """Close out the current session and return its hand-off brief.

        Marks the session note ``done`` (optionally with a human summary) and
        returns the brief a future session would read for this project. If
        nothing was ever logged this session, there is no note to close — it was
        never created (see ``__init__``) — so this is a plain no-op read of the
        brief rather than writing an empty, immediately-``done`` session file.
        """

        path = self.vault.session_path(self.config.project, self.config.session_id)
        if path.exists():
            meta, _ = self.vault.read_session(self.config.project, self.config.session_id)
            meta.status = "done"
            if summary:
                meta.summary = summary
            self.vault.update_session_meta(meta)
        return self.get_brief()

    def get_brief(
        self,
        project: str | None = None,
        token_budget: int | None = None,
    ) -> Brief:
        proj = project or self.config.project
        budget = token_budget if token_budget is not None else self.config.token_budget
        return build_brief(
            self.index.events_for(proj),
            project=proj,
            token_budget=budget,
            entity_summary=lambda name: self.vault.entity_summary(proj, name),
            retired_ids=self.index.all_superseded_ids(),
        )

    def search(
        self,
        query: str,
        *,
        scope: Scope = "all",
        limit: int = 10,
        mode: Mode | None = None,
    ) -> list[SearchHit]:
        # Default to hybrid recall when the semantic layer is available,
        # otherwise plain keyword search.
        effective_mode: Mode = mode or ("hybrid" if self.semantic is not None else "keyword")
        return search_memory(
            self.index,
            query,
            scope=scope,
            current_project=self.config.project,
            limit=limit,
            semantic=self.semantic,
            mode=effective_mode,
        )

    def consolidation_candidates(self, project: str, older_than_days: int | None) -> list[str]:
        """Session ids eligible for consolidation (deterministic, no LLM).

        A session qualifies if it is finished (``status == done``), is not the
        current session, and — if ``older_than_days`` is given — started before
        the cutoff.
        """

        cutoff = (
            datetime.now(tz=timezone.utc) - timedelta(days=older_than_days)
            if older_than_days is not None
            else None
        )
        candidates: list[str] = []
        for path in self.vault.iter_session_files(project):
            session_id = path.stem
            if session_id == self.config.session_id:
                continue
            meta, _ = self.vault.read_session(project, session_id)
            if meta.status != "done":
                continue
            if cutoff is not None and meta.started_at >= cutoff:
                continue
            candidates.append(session_id)
        return candidates

    def consolidate(
        self, project: str | None = None, older_than_days: int | None = None
    ) -> ConsolidationResult:
        """Distil old finished sessions into durable entity notes, then archive
        the originals. Opt-in: requires a configured LLM summariser.
        """

        proj = project or self.config.project
        if self.summarizer is None:
            return ConsolidationResult(
                proj, 0, 0, [], note="no LLM configured (set HANDOFF_LLM_MODEL)"
            )

        candidates = self.consolidation_candidates(proj, older_than_days)
        if not candidates:
            return ConsolidationResult(proj, 0, 0, [], note="nothing to consolidate")

        # Distil only ACTIVE events (retracted decisions are not immortalised).
        retired = self.index.all_superseded_ids()
        events: list[Event] = []
        for session_id in candidates:
            _, session_events = self.vault.read_session(proj, session_id)
            events.extend(e for e in session_events if e.id not in retired)

        facts = self.summarizer.distill(events)

        # Archive the originals BEFORE writing the distilled facts. A crash
        # between the two steps then loses facts (recoverable — the sessions
        # are archived, not deleted) rather than duplicating them: archived
        # sessions are no longer candidates, so a re-run distils nothing twice.
        for session_id in candidates:
            self.vault.archive_session(proj, session_id)
            self.index.delete_session(proj, session_id)

        entities: list[str] = []
        for entity, fact in facts:
            self.vault.append_entity(proj, entity, fact)
            if entity not in entities:
                entities.append(entity)

        return ConsolidationResult(
            project=proj,
            sessions_archived=len(candidates),
            facts_written=len(facts),
            entities=entities,
        )
