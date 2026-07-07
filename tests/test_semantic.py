"""Embeddings, the vector store (both backends), and hybrid search."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from handoff_mcp.config import HandoffConfig
from handoff_mcp.embeddings import HashingEmbedder, cosine
from handoff_mcp.engine import HandoffEngine
from handoff_mcp.models import EventType
from handoff_mcp.semantic import SemanticStore


class CountingEmbedder:
    """Wraps HashingEmbedder and records how many texts it embedded."""

    def __init__(self, dim: int = 32) -> None:
        self._inner = HashingEmbedder(dim)
        self.dim = dim
        self.count = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.count += len(texts)
        return self._inner.embed(texts)


# --- embeddings ----------------------------------------------------------
def test_hashing_embedder_is_deterministic_and_unit_norm() -> None:
    emb = HashingEmbedder(dim=128)
    v1 = emb.embed(["use sqlite for storage"])[0]
    v2 = emb.embed(["use sqlite for storage"])[0]
    assert v1 == v2  # stable across calls (BLAKE2b, not salted hash())
    assert len(v1) == 128
    assert cosine(v1, v1) == pytest.approx(1.0)


def test_shared_tokens_score_higher_than_unrelated() -> None:
    emb = HashingEmbedder(dim=512)
    base, related, unrelated = emb.embed(
        [
            "store transactions in sqlite",
            "sqlite transaction storage layer",
            "schedule a monthly email summary",
        ]
    )
    assert cosine(base, related) > cosine(base, unrelated)


# --- vector store (brute-force backend) ----------------------------------
def _store(prefer_vec: bool) -> SemanticStore:
    conn = sqlite3.connect(":memory:")
    return SemanticStore(conn, HashingEmbedder(dim=256), prefer_vec=prefer_vec)


def test_bruteforce_search_ranks_by_similarity() -> None:
    store = _store(prefer_vec=False)
    assert store.accelerated is False
    store.upsert("e1", "use sqlite database for transactions")
    store.upsert("e2", "deploy the website with docker containers")
    results = store.search("sqlite transactions", limit=2)
    assert results[0][0] == "e1"
    assert results[0][1] > results[1][1]


def test_candidate_filter_restricts_results() -> None:
    store = _store(prefer_vec=False)
    store.upsert("e1", "alpha alpha alpha")
    store.upsert("e2", "alpha alpha beta")
    ids = {eid for eid, _ in store.search("alpha", candidate_ids={"e2"}, limit=5)}
    assert ids == {"e2"}


# --- incremental rebuild + cache invalidation ----------------------------
def test_rebuild_only_embeds_new_events() -> None:
    emb = CountingEmbedder()
    store = SemanticStore(sqlite3.connect(":memory:"), emb, prefer_vec=False)

    assert store.rebuild([("e1", "alpha"), ("e2", "beta")]) == 2
    assert emb.count == 2
    # Re-running with one new event embeds only that one.
    assert store.rebuild([("e1", "alpha"), ("e2", "beta"), ("e3", "gamma")]) == 1
    assert emb.count == 3


def test_rebuild_drops_removed_events() -> None:
    store = SemanticStore(sqlite3.connect(":memory:"), HashingEmbedder(32), prefer_vec=False)
    store.rebuild([("e1", "alpha"), ("e2", "beta")])
    store.rebuild([("e1", "alpha")])  # e2 gone from the vault
    ids = {eid for eid, _ in store.search("alpha", limit=10)}
    assert ids == {"e1"}


def test_cache_invalidated_when_embedding_dim_changes(tmp_path: Path) -> None:
    db = str(tmp_path / "idx.db")
    s1 = SemanticStore(sqlite3.connect(db), HashingEmbedder(16), prefer_vec=False)
    s1.rebuild([("e1", "alpha")])
    s1.conn.close()
    # Reopen with a different embedding width — stale vectors must be wiped.
    emb2 = CountingEmbedder(dim=64)
    s2 = SemanticStore(sqlite3.connect(db), emb2, prefer_vec=False)
    assert s2.rebuild([("e1", "alpha")]) == 1  # re-embedded, not reused
    assert emb2.count == 1


# --- vector store (sqlite-vec accelerated backend) -----------------------
def test_sqlite_vec_backend_matches_bruteforce() -> None:
    pytest.importorskip("sqlite_vec")
    accel = _store(prefer_vec=True)
    if not accel.accelerated:  # extension present but not loadable on this build
        pytest.skip("sqlite-vec not loadable in this environment")

    docs = {
        "e1": "use sqlite database for transactions",
        "e2": "deploy the website with docker",
        "e3": "write the sqlite schema and ingest function",
    }
    brute = _store(prefer_vec=False)
    for eid, text in docs.items():
        accel.upsert(eid, text)
        brute.upsert(eid, text)

    top_accel = [eid for eid, _ in accel.search("sqlite schema", limit=3)]
    top_brute = [eid for eid, _ in brute.search("sqlite schema", limit=3)]
    assert top_accel[0] == top_brute[0]


# --- hybrid search through the engine ------------------------------------
@pytest.fixture
def semantic_engine(config: HandoffConfig) -> Iterator[HandoffEngine]:
    eng = HandoffEngine(replace(config, enable_semantic=True))
    try:
        yield eng
    finally:
        eng.close()


def test_engine_enables_semantic_layer(semantic_engine: HandoffEngine) -> None:
    assert semantic_engine.semantic is not None


def test_hybrid_search_finds_relevant_event(semantic_engine: HandoffEngine) -> None:
    semantic_engine.log_event(
        type=EventType.DECISION, content="Persist transactions in a SQLite database."
    )
    semantic_engine.log_event(type=EventType.FILE, content="Edited the deployment Dockerfile.")
    hits = semantic_engine.search("sqlite transaction storage", scope="current")
    assert any("SQLite" in h.event.content for h in hits)


def test_semantic_mode_survives_rebuild(semantic_engine: HandoffEngine) -> None:
    semantic_engine.log_event(type=EventType.GOAL, content="build the finance summary agent")
    # Fresh engine on the same vault rebuilds vectors from disk.
    reopened = HandoffEngine(replace(semantic_engine.config, enable_semantic=True))
    try:
        hits = reopened.search("finance agent", scope="current", mode="semantic")
        assert any("finance" in h.event.content for h in hits)
    finally:
        reopened.close()


# --- zero-vector handling --------------------------------------------------
def test_zero_vector_query_returns_empty() -> None:
    store = SemanticStore(sqlite3.connect(":memory:"), HashingEmbedder(32), prefer_vec=False)
    store.upsert("e1", "hello world")
    assert store.search("hello")  # sanity: real query matches
    assert store.search("   !!!   ") == []  # no embeddable tokens → nothing


def test_zero_vector_documents_are_not_returned() -> None:
    store = SemanticStore(sqlite3.connect(":memory:"), HashingEmbedder(32), prefer_vec=False)
    store.upsert("punct", "!!! ??? ...")  # tokenless → zero vector
    store.upsert("real", "hello world")
    ids = {eid for eid, _ in store.search("hello", limit=10)}
    assert "punct" not in ids
