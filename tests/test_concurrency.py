"""Concurrency: the engine shares one SQLite connection between the keyword
index and the optional semantic store, and an MCP transport may dispatch tool
calls from worker threads. Without serialisation, interleaved statements on the
shared connection corrupt transactions and drop writes. These tests pin that the
access is now serialised by a real lock.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from handoff_mcp.config import HandoffConfig
from handoff_mcp.engine import HandoffEngine
from handoff_mcp.models import EventType


def _hammer(engine: HandoffEngine, *, threads: int, per_thread: int) -> list[str]:
    """Run concurrent writers+readers; return any exception reprs they hit."""

    errors: list[str] = []

    def worker(n: int) -> None:
        try:
            for i in range(per_thread):
                engine.log_event(
                    type=EventType.DECISION,
                    content=f"thread {n} event {i} alpha beta",
                )
                engine.search("alpha", scope="current")
                engine.get_brief()
        except Exception as exc:  # we want to surface ANY failure, not just SQLite ones
            errors.append(repr(exc))

    workers = [threading.Thread(target=worker, args=(n,)) for n in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    return errors


@pytest.mark.parametrize("semantic", [False, True])
def test_concurrent_writes_and_reads_do_not_corrupt(tmp_path: Path, semantic: bool) -> None:
    config = HandoffConfig(
        vault_path=tmp_path / "vault",
        project="bench",
        session_id="s_conc",
        enable_semantic=semantic,
        embedder="hashing",
    )
    engine = HandoffEngine(config)
    try:
        threads, per_thread = 8, 40
        errors = _hammer(engine, threads=threads, per_thread=per_thread)
        assert errors == [], f"concurrent access raised: {errors[:5]}"
        # Every write must survive — no lost updates from interleaved transactions.
        stored = engine.index.events_for("bench")
        assert len(stored) == threads * per_thread
    finally:
        engine.close()
