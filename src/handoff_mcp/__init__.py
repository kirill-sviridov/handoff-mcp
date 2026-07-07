"""handoff-mcp: persistent cross-session memory and hand-off for Claude over MCP."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("handoff-mcp")
except PackageNotFoundError:  # pragma: no cover - running from a raw checkout
    __version__ = "0.0.0.dev0"

from .config import HandoffConfig
from .engine import HandoffEngine
from .models import Brief, Event, EventType, SearchHit

__all__ = [
    "Brief",
    "Event",
    "EventType",
    "HandoffConfig",
    "HandoffEngine",
    "SearchHit",
    "__version__",
]
