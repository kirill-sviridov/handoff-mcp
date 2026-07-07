"""Paced, narrated version of the two-session demo — made for recording a GIF.

Same behaviour as `two_sessions_demo.py`, but it plays out with pauses and
captions so a screen recording reads like a story. Deterministic, offline.

    python examples/demo_presentation.py

Tips for recording (e.g. with ScreenToGif on Windows):
- Use a wide-ish terminal; run the command, capture until "Nothing re-explained."
- Set SPEED to taste: `SPEED=1.5 python examples/demo_presentation.py` (faster),
  `SPEED=0.7 ...` (slower). Default plays in ~25s.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from handoff_mcp.brief import render_brief
from handoff_mcp.config import HandoffConfig, new_session_id
from handoff_mcp.engine import HandoffEngine
from handoff_mcp.models import Event, EventType

# Enable ANSI colours on Windows 10+ terminals.
if os.name == "nt":
    os.system("")

_SPEED = float(os.environ.get("SPEED", "1.0")) or 1.0
BOLD, DIM, GREEN, CYAN, YELLOW, RESET = (
    "\033[1m",
    "\033[2m",
    "\033[32m",
    "\033[36m",
    "\033[33m",
    "\033[0m",
)


def pause(seconds: float) -> None:
    time.sleep(seconds / _SPEED)


def banner(text: str) -> None:
    print(f"\n{BOLD}{CYAN}{'=' * 64}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 64}{RESET}")
    pause(0.8)


def caption(text: str) -> None:
    print(f"{YELLOW}  >> {text}{RESET}")
    pause(1.0)


def _line(kind: str, content: str, *, retract: bool = False) -> None:
    mark = f"{DIM}(supersedes earlier){RESET}" if retract else ""
    print(f"  {GREEN}+{RESET} {DIM}{kind:<10}{RESET} {content} {mark}")
    pause(0.7)


def emit(
    engine: HandoffEngine,
    etype: EventType,
    content: str,
    *,
    supersedes: list[str] | None = None,
    retract: bool = False,
) -> Event:
    """Log an event AND show it — so the narration always matches real memory."""

    ev = engine.log_event(type=etype, content=content, supersedes=supersedes)
    _line(etype.value, content, retract=retract)
    return ev


def print_brief(text: str) -> None:
    for line in text.splitlines():
        colour = GREEN if line.startswith("#") else ""
        print(f"    {colour}{line}{RESET}")
        pause(0.18)


def run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        project = "agent-hub"

        banner("handoff-mcp  —  two sessions, one memory")
        pause(0.6)

        # ---- Session 1 ---------------------------------------------------
        banner("SESSION 1   (working, then we stop)")
        s1 = HandoffEngine(HandoffConfig(vault_path=vault, project=project))
        emit(s1, EventType.GOAL, "Ship the finance agent with scheduled summaries.")
        first = emit(s1, EventType.DECISION, "Store transactions in a flat JSON file.")
        emit(
            s1,
            EventType.DECISION,
            "Use SQLite instead of JSON - need aggregate queries.",
            supersedes=[first.id],
            retract=True,
        )
        s1.note_entity("Architecture", "SQLite-backed; summaries run in a worker.")
        _line("note", "[[Architecture]]: SQLite-backed; worker for summaries.")
        emit(
            s1,
            EventType.DEADEND,
            "the provider's streaming API times out on long months; needs chunking.",
        )
        emit(s1, EventType.QUESTION, "Recurring transactions: templates or rows?")
        emit(s1, EventType.NEXT_STEP, "Write the SQLite schema, then the ingest function.")
        s1.checkpoint(summary="Chose SQLite; schema is next.")
        print()
        caption("checkpoint  ->  session saved. Closing Claude.")
        s1.close()

        # ---- Session 2 ---------------------------------------------------
        banner("SESSION 2   (fresh start, new session id, same vault)")
        s2 = HandoffEngine(
            HandoffConfig(vault_path=vault, project=project, session_id=new_session_id())
        )
        caption("get_brief()  ->  the next session already knows everything:")
        brief = s2.get_brief()
        print_brief(render_brief(brief))
        pause(0.4)
        caption("The retracted 'flat JSON' decision is GONE (temporal supersession).")
        caption("It knows the goal, the next step, and the dead-end - you re-explained nothing.")

        # ---- cross-project recall ---------------------------------------
        s2.log_event(
            type=EventType.DECISION,
            content="In the Hermes project we fixed timeouts by chunking requests.",
            project="hermes",
        )
        banner("And memory spans projects")
        caption('search_memory("timeout chunking", scope="all"):')
        for hit in s2.search("timeout chunking", scope="all"):
            print(f"    {GREEN}-{RESET} [{hit.event.project}] {hit.event.content}")
            pause(0.4)
        s2.close()

        print(f"\n{BOLD}{GREEN}  All from memory. Nothing re-explained.{RESET}")
        print(f"{DIM}  github.com/kirill-sviridov/handoff-mcp{RESET}\n")
        pause(0.5)


if __name__ == "__main__":
    run()
