"""Optional LLM summariser for memory consolidation ("sleep").

Consolidation is the *only* place an LLM writes into memory, and it is strictly
opt-in (a model must be configured via ``HANDOFF_LLM_MODEL`` + an OpenAI-compatible
endpoint). The deterministic brief, search, and supersession never call it.

The :class:`Summarizer` protocol keeps the backend pluggable (mirroring the
embedder design); :class:`OpenAISummarizer` works with any OpenAI-compatible
endpoint (OpenAI, Together, …). A client can be injected for testing.

``distill`` turns a batch of (active) events into ``(entity_name, fact)`` pairs —
durable, deduplicated project knowledge to append to entity notes — so that
old episodic sessions can be archived without losing what matters.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol, runtime_checkable

from .models import Event

# Entity buckets the distilled knowledge is sorted into. Free-form is allowed,
# but steering the model toward a small stable set keeps the vault tidy.
_ENTITY_HINTS = ["Architecture", "Conventions", "Decisions", "Open Questions"]

_SYSTEM_PROMPT = (
    "You compress an engineering project's session log into durable, long-lived "
    "knowledge. Given a list of events (decisions, dead-ends, files, questions, "
    "next steps), output the lasting facts a future session needs — and drop "
    "ephemera. Group each fact under a short entity name (prefer: "
    f"{', '.join(_ENTITY_HINTS)}; invent others only if clearly warranted). "
    "Preserve dead-ends as cautionary facts. Be concise and non-redundant. "
    'Respond with JSON: {"items": [{"entity": "<name>", "fact": "<one line>"}]}.'
)


@runtime_checkable
class Summarizer(Protocol):
    """Distils events into durable (entity, fact) knowledge pairs."""

    def distill(self, events: list[Event]) -> list[tuple[str, str]]: ...


def _render_events(events: list[Event]) -> str:
    return "\n".join(f"- [{e.type.value}] (imp:{e.importance}) {e.content}" for e in events)


class OpenAISummarizer:
    """Consolidation backend over any OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        prompt_suffix: str | None = None,
        client: Any = None,
    ) -> None:
        self.model = model
        self._client = client
        self._base_url = base_url
        self._api_key = api_key
        # Optional text appended to the user message — handy for model-specific
        # control tokens (e.g. "/no_think" to disable reasoning on Qwen models).
        self._prompt_suffix = prompt_suffix

    def _ensure_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=self._base_url, api_key=self._api_key)
        return self._client

    def distill(self, events: list[Event]) -> list[tuple[str, str]]:
        if not events:
            return []
        user_content = _render_events(events)
        if self._prompt_suffix:
            user_content = f"{user_content}\n\n{self._prompt_suffix}"
        response = self._ensure_client().chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        return _parse_items(content)


def _parse_items(content: str) -> list[tuple[str, str]]:
    """Parse the model's JSON into (entity, fact) pairs, defensively."""

    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return []
    items = data.get("items", []) if isinstance(data, dict) else data
    pairs: list[tuple[str, str]] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                entity = str(item.get("entity", "")).strip()
                fact = str(item.get("fact", "")).strip()
                if entity and fact:
                    pairs.append((entity, fact))
    return pairs


def make_summarizer(
    model: str | None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
) -> Summarizer | None:
    """Build a summariser from config, or ``None`` if no model is configured."""

    if not model:
        return None
    return OpenAISummarizer(
        model,
        base_url=base_url or os.environ.get("OPENAI_BASE_URL"),
        api_key=api_key or os.environ.get("OPENAI_API_KEY"),
    )
