"""Pluggable text embeddings for optional semantic recall.

The deterministic core of handoff-mcp never needs embeddings. Semantic recall is
an *optional* layer (``pip install handoff-mcp[semantic]``) that augments keyword
search.

Whether it recalls on *meaning* depends on the backend. The default
:class:`HashingEmbedder` is a **lexical** baseline: it feature-hashes tokens, so
it adds fuzzy lexical matching and exercises the hybrid pipeline, but it does NOT
understand paraphrases that share no literal terms. It is dependency-free,
deterministic, and offline — ideal for demos and reproducible tests, not a stand-in
for a learned model. For genuine paraphrase-tolerant recall, use
:class:`SentenceTransformerEmbedder` (``local``) or :class:`OpenAIEmbedder`
(``openai``); the :class:`Embedder` protocol lets any of them drop in without
changing anything else.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Any, Protocol, runtime_checkable

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


@runtime_checkable
class Embedder(Protocol):
    """Anything that turns text into fixed-length unit vectors."""

    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one ``dim``-length vector per input string."""
        ...


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class HashingEmbedder:
    """Deterministic feature-hashing embedder (the "hashing trick").

    Each token is hashed to a bucket and a sign via BLAKE2b (stable across
    processes, unlike Python's salted ``hash()``), accumulated, then L2-
    normalised. This captures lexical overlap robustly and is good enough to
    demonstrate hybrid keyword+semantic recall; it is not a substitute for a
    learned model when true paraphrase understanding matters.
    """

    def __init__(self, dim: int = 256) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim

    def _hash(self, token: str) -> tuple[int, float]:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        bucket = value % self.dim
        sign = 1.0 if (value >> 63) & 1 else -1.0
        return bucket, sign

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _tokenize(text):
            bucket, sign = self._hash(token)
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (0 if either is zero)."""

    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def l2_normalize(vec: list[float]) -> list[float]:
    """Scale a vector to unit length (returns it unchanged if it is all zeros).

    All embedders normalise their output so the vector store can treat L2
    distance and cosine similarity interchangeably (see semantic.py).
    """

    norm = math.sqrt(sum(v * v for v in vec))
    return vec if norm == 0.0 else [v / norm for v in vec]


class OpenAIEmbedder:
    """Embeddings via any OpenAI-compatible endpoint (OpenAI, Together, …).

    Lightweight: only needs an HTTP client (`handoff-mcp[semantic-openai]`). The
    `openai` SDK is imported lazily so it is required only when this backend is
    actually used. A `client` can be injected for testing.
    """

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        dimensions: int | None = None,
        client: Any = None,
    ) -> None:
        self.model = model
        self._client = client
        self._base_url = base_url
        self._api_key = api_key
        self._dimensions = dimensions
        # Discover the embedding width up front (the vector store needs it to
        # create its fixed-width table) — skip the probe if it was given.
        self.dim = (
            dimensions if dimensions is not None else len(self._embed_raw(["dimension probe"])[0])
        )

    def _ensure_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=self._base_url, api_key=self._api_key)
        return self._client

    def _embed_raw(self, texts: list[str]) -> list[list[float]]:
        kwargs: dict[str, Any] = {"model": self.model, "input": texts}
        if self._dimensions is not None:
            kwargs["dimensions"] = self._dimensions
        response = self._ensure_client().embeddings.create(**kwargs)
        return [list(item.embedding) for item in response.data]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [l2_normalize([float(x) for x in vec]) for vec in self._embed_raw(texts)]


# A strong small multilingual model (incl. Russian) as of 2026: solid
# MMTEB, Apache-2.0, native sentence-transformers support, no document prefix.
DEFAULT_LOCAL_MODEL = "Qwen/Qwen3-Embedding-0.6B"


class SentenceTransformerEmbedder:
    """Local, offline embeddings via sentence-transformers.

    Needs `handoff-mcp[semantic-local]` (pulls in torch). The model is loaded
    lazily; a `model` object can be injected for testing. ``trust_remote_code``
    is required by some models (e.g. gte-multilingual-base).
    """

    def __init__(
        self,
        model_name: str = DEFAULT_LOCAL_MODEL,
        *,
        model: Any = None,
        trust_remote_code: bool = False,
    ) -> None:
        if model is None:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(model_name, trust_remote_code=trust_remote_code)
        self._model = model
        self.dim = int(model.get_sentence_embedding_dimension())

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [[float(x) for x in vec] for vec in vectors]


def make_embedder(
    name: str = "hashing",
    *,
    model: str | None = None,
    embedding_dim: int = 256,
) -> Embedder:
    """Build an embedder by backend name: ``hashing`` | ``openai`` | ``local``.

    OpenAI settings come from the environment (``HANDOFF_EMBED_BASE_URL`` /
    ``OPENAI_BASE_URL`` and ``HANDOFF_EMBED_API_KEY`` / ``OPENAI_API_KEY``).
    """

    backend = name.lower()
    if backend == "hashing":
        return HashingEmbedder(embedding_dim)
    if backend == "openai":
        return OpenAIEmbedder(
            model or "text-embedding-3-small",
            base_url=os.environ.get("HANDOFF_EMBED_BASE_URL") or os.environ.get("OPENAI_BASE_URL"),
            api_key=os.environ.get("HANDOFF_EMBED_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        )
    if backend == "local":
        trust = os.environ.get("HANDOFF_EMBED_TRUST_REMOTE_CODE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        return SentenceTransformerEmbedder(model or DEFAULT_LOCAL_MODEL, trust_remote_code=trust)
    raise ValueError(f"unknown embedder {name!r} (use 'hashing', 'openai', or 'local')")
