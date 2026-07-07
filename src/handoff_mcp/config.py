"""Runtime configuration: where the vault lives, the derived index, defaults.

Resolution order for every setting is: explicit constructor argument >
environment variable > built-in default. This makes the server trivial to wire
into a Claude Code / Claude Desktop MCP config block while staying fully
injectable from tests.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_TOKEN_BUDGET = 1200
DEFAULT_EMBEDDING_DIM = 256
ENV_VAULT = "HANDOFF_VAULT"
ENV_PROJECT = "HANDOFF_PROJECT"
ENV_TOKEN_BUDGET = "HANDOFF_TOKEN_BUDGET"
ENV_SEMANTIC = "HANDOFF_SEMANTIC"
ENV_EMBEDDING_DIM = "HANDOFF_EMBED_DIM"
ENV_EMBEDDER = "HANDOFF_EMBEDDER"
ENV_EMBED_MODEL = "HANDOFF_EMBED_MODEL"
ENV_LLM_MODEL = "HANDOFF_LLM_MODEL"
ENV_LLM_BASE_URL = "HANDOFF_LLM_BASE_URL"
ENV_LLM_API_KEY = "HANDOFF_LLM_API_KEY"
ENV_AUTO_SYNC = "HANDOFF_AUTO_SYNC"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _default_vault() -> Path:
    env = os.environ.get(ENV_VAULT)
    if env:
        return Path(env).expanduser()
    return Path.home() / ".handoff-mcp" / "vault"


def new_session_id() -> str:
    """A sortable-ish, collision-resistant session id.

    Format: ``s_<utc-compact>_<rand>`` — the timestamp prefix keeps session
    notes naturally ordered on disk for humans browsing the vault.
    """

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"s_{stamp}_{secrets.token_hex(3)}"


@dataclass
class HandoffConfig:
    """All knobs the server and stores need."""

    vault_path: Path = field(default_factory=_default_vault)
    project: str = field(default_factory=lambda: os.environ.get(ENV_PROJECT, "default"))
    session_id: str = field(default_factory=new_session_id)
    token_budget: int = field(
        default_factory=lambda: int(os.environ.get(ENV_TOKEN_BUDGET, DEFAULT_TOKEN_BUDGET))
    )
    enable_semantic: bool = field(default_factory=lambda: _env_flag(ENV_SEMANTIC))
    embedding_dim: int = field(
        default_factory=lambda: int(os.environ.get(ENV_EMBEDDING_DIM, DEFAULT_EMBEDDING_DIM))
    )
    # Embedding backend for the semantic layer: "hashing" | "openai" | "local".
    embedder: str = field(default_factory=lambda: os.environ.get(ENV_EMBEDDER, "hashing"))
    embed_model: str | None = field(default_factory=lambda: os.environ.get(ENV_EMBED_MODEL))
    # LLM for opt-in memory consolidation (OpenAI-compatible). Unset → disabled.
    llm_model: str | None = field(default_factory=lambda: os.environ.get(ENV_LLM_MODEL))
    llm_base_url: str | None = field(
        default_factory=lambda: (
            os.environ.get(ENV_LLM_BASE_URL)
            or os.environ.get("HANDOFF_EMBED_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
        )
    )
    llm_api_key: str | None = field(
        default_factory=lambda: os.environ.get(ENV_LLM_API_KEY) or os.environ.get("OPENAI_API_KEY")
    )
    # Opt-in: git pull on get_brief, git commit+push on checkpoint. Off by
    # default; a no-op when the vault has no remote configured.
    auto_sync: bool = field(default_factory=lambda: _env_flag(ENV_AUTO_SYNC))

    @property
    def db_path(self) -> Path:
        """Derived index lives beside the vault and is fully rebuildable."""

        return self.vault_path / ".index.db"

    def ensure_dirs(self) -> None:
        self.vault_path.mkdir(parents=True, exist_ok=True)
