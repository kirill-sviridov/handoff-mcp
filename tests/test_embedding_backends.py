"""Pluggable embedding backends: factory, adapters (via fakes), engine wiring.

The real OpenAI / sentence-transformers backends are exercised through injected
fakes that mirror their response/return shapes, so these tests need neither
network access nor the heavy optional dependencies.
"""

from __future__ import annotations

import math
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from handoff_mcp.config import HandoffConfig
from handoff_mcp.embeddings import (
    Embedder,
    HashingEmbedder,
    OpenAIEmbedder,
    SentenceTransformerEmbedder,
    cosine,
    l2_normalize,
    make_embedder,
)
from handoff_mcp.engine import HandoffEngine
from handoff_mcp.models import EventType


def _pseudo_vec(text: str, dim: int) -> list[float]:
    """A deterministic, text-dependent vector (stand-in for a learned model)."""

    vec = [0.0] * dim
    for i, ch in enumerate(text):
        vec[(ord(ch) + i) % dim] += 1.0
    return vec


# --- fakes mirroring the external SDK shapes -----------------------------
class _FakeOpenAIEmbeddings:
    def __init__(self, dim: int) -> None:
        self.dim = dim

    def create(self, *, model: str, input: list[str], **kw: Any) -> Any:
        data = [SimpleNamespace(embedding=_pseudo_vec(t, self.dim)) for t in input]
        return SimpleNamespace(data=data)


class _FakeOpenAIClient:
    def __init__(self, dim: int) -> None:
        self.embeddings = _FakeOpenAIEmbeddings(dim)


class _FakeSTModel:
    def __init__(self, dim: int) -> None:
        self._dim = dim

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(self, texts: list[str], normalize_embeddings: bool = False) -> list[list[float]]:
        out = []
        for t in texts:
            v = _pseudo_vec(t, self._dim)
            out.append(l2_normalize(v) if normalize_embeddings else v)
        return out


# --- helpers -------------------------------------------------------------
def _unit(vec: list[float]) -> bool:
    return math.isclose(math.sqrt(sum(x * x for x in vec)), 1.0, rel_tol=1e-6)


# --- OpenAI adapter ------------------------------------------------------
def test_openai_embedder_probes_dim_and_normalizes() -> None:
    emb = OpenAIEmbedder(client=_FakeOpenAIClient(16))
    assert emb.dim == 16
    [vec] = emb.embed(["hello"])
    assert len(vec) == 16
    assert _unit(vec)  # adapters always return unit vectors


def test_openai_embedder_explicit_dimensions_skips_probe() -> None:
    # dimensions given -> no probe call needed to learn the width.
    emb = OpenAIEmbedder(client=_FakeOpenAIClient(8), dimensions=8)
    assert emb.dim == 8


# --- sentence-transformers adapter ---------------------------------------
def test_sentence_transformer_embedder() -> None:
    emb = SentenceTransformerEmbedder(model=_FakeSTModel(12))
    assert emb.dim == 12
    [vec] = emb.embed(["hello"])
    assert len(vec) == 12
    assert _unit(vec)


# --- factory -------------------------------------------------------------
def test_factory_default_is_hashing() -> None:
    emb = make_embedder("hashing", embedding_dim=32)
    assert isinstance(emb, HashingEmbedder)
    assert emb.dim == 32


def test_factory_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="unknown embedder"):
        make_embedder("does-not-exist")


def test_adapters_satisfy_the_protocol() -> None:
    assert isinstance(HashingEmbedder(4), Embedder)
    assert isinstance(OpenAIEmbedder(client=_FakeOpenAIClient(4)), Embedder)
    assert isinstance(SentenceTransformerEmbedder(model=_FakeSTModel(4)), Embedder)


def test_cosine_separates_related_from_unrelated() -> None:
    emb = OpenAIEmbedder(client=_FakeOpenAIClient(64))
    a, b, c = emb.embed(["sqlite storage", "sqlite storage layer", "totally other text"])
    assert cosine(a, b) > cosine(a, c)


# --- engine wiring -------------------------------------------------------
def test_engine_uses_configured_backend(
    config: HandoffConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = HashingEmbedder(64)
    monkeypatch.setattr("handoff_mcp.engine.make_embedder", lambda *a, **k: fake)

    eng = HandoffEngine(replace(config, enable_semantic=True, embedder="openai"))
    try:
        assert eng.semantic is not None
        assert eng.semantic.embedder is fake
        eng.log_event(type=EventType.GOAL, content="build the summary agent")
        assert eng.search("summary agent", scope="current")
    finally:
        eng.close()
