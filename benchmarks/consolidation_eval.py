"""Evaluate models on the consolidation task — can a small/local model do it?

Consolidation (the `consolidate` tool) distils a session's ACTIVE events into
durable (entity, fact) pairs. This harness scores any OpenAI-compatible model on
that exact task, using the *real* OpenAISummarizer we ship, so the comparison
reflects what users actually get.

Metrics (per model, averaged over scenarios):
* **json**         — produced a parseable, non-empty result.
* **coverage**     — fraction of the scenario's key facts present in the output
                     (paraphrase-robust marker check).
* **faithfulness** — fraction of output facts whose words are grounded in the
                     input (catches hallucinated/invented facts). Heuristic.
* **facts / s**    — average number of facts emitted, and latency.

Configure models via env and run (point at API baseline + local ollama):

    OPENAI_BASE_URL=...  OPENAI_API_KEY=...  BENCH_LLM_MODEL=openai/gpt-5-mini \\
    HANDOFF_EVAL_LOCAL=qwen3.5:4b \\
    python benchmarks/consolidation_eval.py
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

from handoff_mcp.models import Event, EventType
from handoff_mcp.summarizer import OpenAISummarizer, Summarizer

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {
    "the",
    "a",
    "an",
    "to",
    "of",
    "in",
    "on",
    "for",
    "and",
    "or",
    "with",
    "we",
    "our",
    "is",
    "are",
    "be",
    "by",
    "as",
    "it",
    "this",
    "that",
    "use",
    "user",
    "uses",
    "using",
    "their",
    "they",
    "system",
    "application",
    "app",
    "into",
    "from",
    "not",
    "no",
    "now",
}


@dataclass
class Scenario:
    name: str
    events: list[Event]
    key_markers: list[str]  # distinctive substrings that MUST survive distillation


def _ev(t: EventType, content: str) -> Event:
    return Event(id="x", session_id="s", project="p", type=t, content=content)


SCENARIOS: list[Scenario] = [
    Scenario(
        name="finance-agent",
        events=[
            _ev(EventType.GOAL, "Ship a finance agent that produces scheduled monthly summaries."),
            _ev(
                EventType.DECISION,
                "Use SQLite for transaction storage to support aggregate queries.",
            ),
            _ev(
                EventType.DECISION, "Run LLM summary calls in a background worker, not the handler."
            ),
            _ev(
                EventType.DEADEND,
                "the provider's streaming API times out on long months; must chunk requests.",
            ),
            _ev(EventType.FILE, "Touched agent/storage.py and agent/worker.py."),
            _ev(EventType.NEXT_STEP, "Write the SQLite schema and the ingest function."),
        ],
        key_markers=["sqlite", "worker", "chunk", "schema", "monthly"],
    ),
    Scenario(
        name="auth-revamp",
        events=[
            _ev(EventType.DECISION, "Authenticate with stateless JWT tokens."),
            _ev(EventType.DEADEND, "Session cookies broke across subdomains; abandoned them."),
            _ev(EventType.DECISION, "Store refresh tokens hashed in the database."),
            _ev(EventType.QUESTION, "Should access tokens be short-lived (15 min)?"),
            _ev(EventType.NEXT_STEP, "Add token rotation to the auth middleware."),
        ],
        key_markers=["jwt", "cookie", "refresh", "rotation"],
    ),
]


def _words(text: str) -> set[str]:
    return {w for w in _TOKEN.findall(text.lower()) if w not in _STOP and len(w) > 2}


@dataclass
class ModelResult:
    label: str
    json_ok: int
    coverage: float
    faithfulness: float
    avg_facts: float
    avg_latency: float
    scenarios: int


def evaluate_model(label: str, summarizer: Summarizer) -> ModelResult:
    json_ok = 0
    cov_sum = 0.0
    faith_sum = 0.0
    facts_sum = 0
    latency_sum = 0.0

    for scenario in SCENARIOS:
        input_words = set().union(*(_words(e.content) for e in scenario.events))
        start = time.monotonic()
        try:
            facts = summarizer.distill(scenario.events)
        except Exception as exc:  # report, don't crash the sweep
            print(f"  [{label}] {scenario.name}: ERROR {type(exc).__name__}: {str(exc)[:120]}")
            facts = []
        latency_sum += time.monotonic() - start

        if facts:
            json_ok += 1
        facts_sum += len(facts)

        blob = " ".join(f"{e} {f}" for e, f in facts).lower()
        cov_sum += sum(m in blob for m in scenario.key_markers) / len(scenario.key_markers)

        if facts:
            grounded = 0
            for _, fact in facts:
                fw = _words(fact)
                if not fw or len(fw & input_words) / len(fw) >= 0.5:
                    grounded += 1
            faith_sum += grounded / len(facts)
        else:
            faith_sum += 0.0

    n = len(SCENARIOS)
    return ModelResult(
        label=label,
        json_ok=json_ok,
        coverage=cov_sum / n,
        faithfulness=faith_sum / n,
        avg_facts=facts_sum / n,
        avg_latency=latency_sum / n,
        scenarios=n,
    )


def _build_models() -> list[tuple[str, Summarizer]]:
    models: list[tuple[str, Summarizer]] = []
    api_model = os.environ.get("BENCH_LLM_MODEL")
    if api_model and os.environ.get("OPENAI_API_KEY"):
        models.append(
            (
                f"API:{api_model}",
                OpenAISummarizer(
                    api_model,
                    base_url=os.environ.get("OPENAI_BASE_URL"),
                    api_key=os.environ.get("OPENAI_API_KEY"),
                ),
            )
        )
    local = os.environ.get("HANDOFF_EVAL_LOCAL", "")
    local_base = os.environ.get("HANDOFF_EVAL_LOCAL_BASE", "http://localhost:11434/v1")
    for entry in (m.strip() for m in local.split(",") if m.strip()):
        # Syntax: "model" or "model|nothink" (appends /no_think to disable Qwen reasoning).
        name, _, flag = entry.partition("|")
        suffix = "/no_think" if flag == "nothink" else None
        label = f"local:{name}" + (" (no-think)" if suffix else "")
        models.append(
            (
                label,
                OpenAISummarizer(name, base_url=local_base, api_key="ollama", prompt_suffix=suffix),
            )
        )
    return models


def main() -> list[ModelResult]:
    models = _build_models()
    if not models:
        print("No models configured. Set BENCH_LLM_MODEL (+OPENAI_*) and/or HANDOFF_EVAL_LOCAL.")
        return []

    print(f"Consolidation eval — {len(SCENARIOS)} scenarios\n")
    results = [evaluate_model(label, s) for label, s in models]

    print(f"\n{'model':<26}{'json':>6}{'coverage':>10}{'faithful':>10}{'facts':>7}{'sec':>8}")
    print("-" * 67)
    for r in results:
        print(
            f"{r.label:<26}{f'{r.json_ok}/{r.scenarios}':>6}{r.coverage * 100:>9.0f}%"
            f"{r.faithfulness * 100:>9.0f}%{r.avg_facts:>7.1f}{r.avg_latency:>8.1f}"
        )
    print("-" * 67)
    print("coverage = key facts retained · faithfulness = facts grounded in input (no invention)")
    return results


if __name__ == "__main__":
    main()
