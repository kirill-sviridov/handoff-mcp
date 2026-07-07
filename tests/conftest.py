"""Shared fixtures: a throwaway vault + engine per test."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from handoff_mcp.config import HandoffConfig
from handoff_mcp.engine import HandoffEngine


@pytest.fixture
def config(tmp_path: Path) -> HandoffConfig:
    return HandoffConfig(
        vault_path=tmp_path / "vault",
        project="proj-a",
        session_id="s_test_0001",
        token_budget=1200,
    )


@pytest.fixture
def engine(config: HandoffConfig) -> Iterator[HandoffEngine]:
    eng = HandoffEngine(config)
    try:
        yield eng
    finally:
        eng.close()
