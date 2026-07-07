"""End-to-end demo: session 2 resumes from session 1's brief.

Run it::

    python examples/two_sessions_demo.py

It uses a throwaway vault in a temp directory so it never touches your real
memory. The point: session 2 starts knowing the goal, the next step, the
decisions (and which one was retracted), and the dead-end session 1 hit — without
re-reading any of session 1's raw context.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from handoff_mcp.brief import render_brief
from handoff_mcp.config import HandoffConfig, new_session_id
from handoff_mcp.engine import HandoffEngine
from handoff_mcp.models import EventType


def run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        project = "agent-hub"

        # ---- Session 1: do some work ------------------------------------
        print("=" * 64)
        print("SESSION 1 — working")
        print("=" * 64)
        s1 = HandoffEngine(HandoffConfig(vault_path=vault, project=project))
        s1.log_event(
            type=EventType.GOAL,
            content="Ship the finance agent: income/expense tracking with scheduled summaries.",
            importance=5,
        )
        first_decision = s1.log_event(
            type=EventType.DECISION,
            content="Store transactions in a flat JSON file for the MVP.",
            importance=3,
        )
        # Later in the same session we change our mind — supersede the old choice.
        s1.log_event(
            type=EventType.DECISION,
            content=(
                "Use SQLite for transactions instead of JSON — need queries for "
                "summaries. See [[Architecture]]."
            ),
            importance=4,
            supersedes=[first_decision.id],
        )
        # Durable project knowledge — surfaces in the brief's "Related knowledge".
        s1.note_entity(
            "Architecture",
            "SQLite-backed storage; LLM summary calls run in a background worker.",
        )
        s1.log_event(
            type=EventType.DEADEND,
            content=(
                "Tried the provider's streaming API for the summary job — times out on long "
                "months. Don't retry without chunking."
            ),
            importance=4,
        )
        s1.log_event(
            type=EventType.QUESTION,
            content="Should recurring transactions be modelled as templates or materialised rows?",
            importance=2,
        )
        s1.log_event(
            type=EventType.NEXT_STEP,
            content=(
                "Write the SQLite schema for transactions and categories, then the ingest function."
            ),
            importance=5,
        )
        s1.checkpoint(summary="Chose SQLite; finance schema is next.")
        s1.close()
        print("Logged 6 events; checkpoint produced a brief.\n")

        # ---- Session 2: a fresh process resumes -------------------------
        print("=" * 64)
        print("SESSION 2 — resuming (new session id, same vault)")
        print("=" * 64)
        s2 = HandoffEngine(
            HandoffConfig(vault_path=vault, project=project, session_id=new_session_id())
        )
        brief = s2.get_brief()
        print(render_brief(brief))

        # Prove the retracted decision is gone and the live one is present.
        body = render_brief(brief)
        assert "flat JSON file" not in body, "retracted decision leaked into the brief!"
        assert "Use SQLite" in body, "active decision missing from the brief!"
        assert "Don't retry without chunking" in body, "dead-end missing!"
        assert "## Related knowledge" in body, "graph-linked entity missing!"
        assert "background worker" in body, "entity summary missing!"
        print("[ok] retracted JSON decision excluded; live SQLite decision kept.")
        print("[ok] linked [[Architecture]] entity surfaced under Related knowledge.")
        print(f"[ok] brief is ~{brief.estimated_tokens} tokens (budget {brief.token_budget}).")

        # Cross-project recall: session 2 references work from ANOTHER project.
        s2.log_event(
            type=EventType.DECISION,
            content="In the Hermes project we solved long-job timeouts by chunking requests.",
            importance=3,
            project="hermes",
        )
        hits = s2.search("timeout chunking", scope="all")
        print("\nCross-project search('timeout chunking', scope=all):")
        for h in hits:
            print(f"  - [{h.event.project}] {h.event.content}")
        assert any(h.event.project == "hermes" for h in hits), "cross-project recall failed!"
        print("[ok] recalled a decision from a different project.")
        s2.close()

        print("\nAll demo assertions passed.")


if __name__ == "__main__":
    run()
