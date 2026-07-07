"""Optional semantic vector store for paraphrase-tolerant recall.

This is the persistence + nearest-neighbour layer behind hybrid search. It is
**optional**: the deterministic brief and keyword search never touch it.

Design:

* Vectors are always persisted as BLOBs in an ordinary ``event_vectors`` table —
  no extension required, so the store is portable and rebuildable.
* If the ``sqlite-vec`` extension is installed *and* loadable, a ``vec0`` virtual
  table is maintained alongside for accelerated KNN; it is rebuilt from the BLOB
  table on startup. If not, search falls back to an exact pure-Python cosine
  scan, which is plenty fast at personal-memory scale.

Either way the *results* are the same ranking; ``sqlite-vec`` only changes how
fast the nearest neighbours are found.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from array import array
from collections.abc import Callable, Iterable, Sequence

from .embeddings import Embedder, cosine


def _to_blob(vec: Sequence[float]) -> bytes:
    return array("f", vec).tobytes()


def _from_blob(blob: bytes) -> list[float]:
    arr = array("f")
    arr.frombytes(blob)
    return list(arr)


def _is_zero(vec: Sequence[float]) -> bool:
    """A vector with no magnitude (e.g. content with no embeddable tokens).

    Zero vectors are excluded from the index so the sqlite-vec L2 fast path and
    the brute-force cosine path agree (an L2 distance to a zero vector would
    otherwise be misread as ~0.5 similarity, while cosine is 0)."""

    return not any(vec)


def _l2_to_cosine(distance: float) -> float:
    """For unit vectors, ||a-b||^2 = 2 - 2cos, so cos = 1 - d^2/2."""

    return 1.0 - (distance * distance) / 2.0


class SemanticStore:
    """Embedding storage + KNN, with an optional sqlite-vec fast path."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        embedder: Embedder,
        *,
        prefer_vec: bool = True,
        lock: threading.RLock | None = None,
    ) -> None:
        self.conn = conn
        self.embedder = embedder
        self.dim = embedder.dim
        # The semantic store shares the keyword index's connection, so it must
        # share its lock too — otherwise the two would serialise independently
        # and could still interleave statements on the one connection. The engine
        # passes the SqliteIndex's lock; a standalone store gets its own.
        self.lock = lock if lock is not None else threading.RLock()
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS event_vectors ("
            "event_id TEXT PRIMARY KEY, vec BLOB NOT NULL)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS vector_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        # Cached vectors are only valid for the embedding width that produced
        # them. If the configured embedder changed dimension, the cache (and any
        # vec0 table built for the old width) is stale — drop it for a clean
        # re-embed. The signature also captures a backend-type switch.
        signature = f"{type(embedder).__name__}:{self.dim}"
        self._invalidate_if_signature_changed(signature)

        self._serialize: Callable[[Sequence[float]], bytes] | None = None
        self._vec_enabled = self._try_enable_vec() if prefer_vec else False
        if self._vec_enabled:
            self._rebuild_vec_from_blobs()

    def _invalidate_if_signature_changed(self, signature: str) -> None:
        row = self.conn.execute("SELECT value FROM vector_meta WHERE key = 'embedder'").fetchone()
        if row is not None and row[0] != signature:
            self.conn.execute("DELETE FROM event_vectors")
            self.conn.execute("DROP TABLE IF EXISTS vec_events")
        self.conn.execute(
            "INSERT INTO vector_meta(key, value) VALUES ('embedder', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (signature,),
        )
        self.conn.commit()

    @property
    def accelerated(self) -> bool:
        """True if the sqlite-vec KNN fast path is active."""

        return self._vec_enabled

    def _try_enable_vec(self) -> bool:
        try:
            import sqlite_vec

            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            self._serialize = sqlite_vec.serialize_float32
            self.conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_events "
                f"USING vec0(event_id TEXT PRIMARY KEY, embedding float[{self.dim}])"
            )
            return True
        except Exception:
            with contextlib.suppress(Exception):
                self.conn.enable_load_extension(False)
            return False

    def _rebuild_vec_from_blobs(self) -> None:
        self.conn.execute("DELETE FROM vec_events")
        rows = self.conn.execute("SELECT event_id, vec FROM event_vectors").fetchall()
        for event_id, blob in rows:
            self._insert_vec(event_id, _from_blob(blob))
        self.conn.commit()

    def _insert_vec(self, event_id: str, vec: list[float]) -> None:
        assert self._serialize is not None
        self.conn.execute("DELETE FROM vec_events WHERE event_id = ?", (event_id,))
        if _is_zero(vec):
            return  # keep the BLOB cache, but don't index a magnitude-less vector
        self.conn.execute(
            "INSERT INTO vec_events(event_id, embedding) VALUES (?, ?)",
            (event_id, self._serialize(vec)),
        )

    # --- writes ----------------------------------------------------------
    def upsert(self, event_id: str, text: str) -> None:
        vec = self.embedder.embed([text])[0]
        with self.lock:
            self.conn.execute(
                "INSERT INTO event_vectors(event_id, vec) VALUES (?, ?) "
                "ON CONFLICT(event_id) DO UPDATE SET vec = excluded.vec",
                (event_id, _to_blob(vec)),
            )
            if self._vec_enabled:
                self._insert_vec(event_id, vec)
            self.conn.commit()

    def rebuild(self, items: Iterable[tuple[str, str]]) -> int:
        """Sync the store to ``items`` (event_id, text), embedding incrementally.

        Events are immutable (a change is a new event that supersedes the old),
        so an id already in the cache never needs re-embedding. Only new ids are
        embedded and ids no longer present are dropped — so startup cost is
        proportional to *new* events, not the whole vault. Returns the count of
        newly embedded events.
        """

        pairs = list(items)
        incoming = {event_id for event_id, _ in pairs}
        with self.lock:
            existing = {
                row[0] for row in self.conn.execute("SELECT event_id FROM event_vectors").fetchall()
            }

            # Drop vectors for events that no longer exist.
            for stale_id in existing - incoming:
                self.conn.execute("DELETE FROM event_vectors WHERE event_id = ?", (stale_id,))
                if self._vec_enabled:
                    self.conn.execute("DELETE FROM vec_events WHERE event_id = ?", (stale_id,))

            to_embed = [(eid, text) for eid, text in pairs if eid not in existing]
            if to_embed:
                vectors = self.embedder.embed([text for _, text in to_embed])
                for (event_id, _), vec in zip(to_embed, vectors, strict=True):
                    self.conn.execute(
                        "INSERT INTO event_vectors(event_id, vec) VALUES (?, ?)",
                        (event_id, _to_blob(vec)),
                    )
                    if self._vec_enabled:
                        self._insert_vec(event_id, vec)
            self.conn.commit()
            return len(to_embed)

    # --- reads -----------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        candidate_ids: set[str] | None = None,
        limit: int = 10,
    ) -> list[tuple[str, float]]:
        """Return (event_id, cosine_similarity) pairs, best first."""

        qvec = self.embedder.embed([query])[0]
        if _is_zero(qvec):
            return []  # a query with no embeddable content matches nothing meaningful

        # Fast path: global KNN via sqlite-vec when we aren't filtering to a
        # candidate subset (filtering is cheaper to do exactly in Python).
        if self._vec_enabled and candidate_ids is None and self._serialize is not None:
            with self.lock:
                rows = self.conn.execute(
                    "SELECT event_id, distance FROM vec_events "
                    "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                    (self._serialize(qvec), limit),
                ).fetchall()
            return [(eid, _l2_to_cosine(dist)) for eid, dist in rows]

        # Exact fallback: cosine over stored BLOBs.
        if candidate_ids is not None:
            if not candidate_ids:
                return []
            placeholders = ",".join("?" for _ in candidate_ids)
            with self.lock:
                rows = self.conn.execute(
                    f"SELECT event_id, vec FROM event_vectors WHERE event_id IN ({placeholders})",
                    tuple(candidate_ids),
                ).fetchall()
        else:
            with self.lock:
                rows = self.conn.execute("SELECT event_id, vec FROM event_vectors").fetchall()

        scored = []
        for eid, blob in rows:
            vec = _from_blob(blob)
            if _is_zero(vec):
                continue  # consistent with the vec0 path, which never indexes these
            scored.append((eid, cosine(qvec, vec)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]
