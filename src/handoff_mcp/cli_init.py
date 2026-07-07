"""`handoff-init` — drop the handoff-mcp usage block into a project's agent file.

So a project's agents (any client, with or without the skill) know to use memory.
Idempotent: if the block is already there it does nothing; otherwise it appends
(or creates the file), never clobbering existing content.

    handoff-init                       # CLAUDE.md in the current dir
    handoff-init --client codex        # AGENTS.md
    handoff-init --client cursor       # .cursor/rules/handoff.mdc
    handoff-init --file docs/AGENTS.md # explicit path
"""

from __future__ import annotations

import argparse
from pathlib import Path

# Marker lets us detect an existing block (idempotency) without false positives.
MARKER = "<!-- handoff-mcp -->"

HANDOFF_BLOCK = f"""{MARKER}
## Memory (handoff-mcp)
At the START of a session, call `get_brief` and resume from the next step — don't
redo settled decisions or repeat recorded dead-ends. As we work, proactively
`log_event` goals / decisions (+why) / dead-ends / files / questions / next step
— ONE atomic item per call (1-2 sentences), not a whole-session summary;
reference durable notes inline as `[[Entity]]`. If a decision is reversed, log
the new one with `supersedes` = the old event id. When the user mentions past or
other-project work, call `search_memory`. On "stopping / го дальше / на сегодня
всё", call `checkpoint`. If the user wants their memory on another device, use
the `sync` tool (or `handoff-sync --setup <private-repo-url>`); set
`HANDOFF_AUTO_SYNC=1` for automatic pull-on-brief / push-on-checkpoint. No git
= local-only, still fully works.
"""

CLIENT_FILES: dict[str, str] = {
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
    "generic": "AGENTS.md",
    "cursor": ".cursor/rules/handoff.mdc",
    "windsurf": ".windsurfrules",
}


def ensure_block(path: Path) -> str:
    """Ensure ``path`` contains the handoff block. Returns the action taken:
    'present' | 'appended' | 'created'."""

    if path.exists():
        text = path.read_text(encoding="utf-8")
        if MARKER in text:
            return "present"
        path.write_text(text.rstrip() + "\n\n" + HANDOFF_BLOCK, encoding="utf-8")
        return "appended"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HANDOFF_BLOCK, encoding="utf-8")
    return "created"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="handoff-init", description=__doc__)
    parser.add_argument(
        "--client",
        choices=sorted(CLIENT_FILES),
        default="claude",
        help="Which client's instruction file to target (default: claude).",
    )
    parser.add_argument("--file", help="Explicit target file (overrides --client).")
    parser.add_argument("--dir", default=".", help="Project directory (default: current).")
    args = parser.parse_args(argv)

    target = Path(args.file) if args.file else Path(args.dir) / CLIENT_FILES[args.client]
    action = ensure_block(target)
    verb = {
        "present": "already present in",
        "appended": "appended to",
        "created": "created",
    }[action]
    print(f"handoff block {verb} {target}")


if __name__ == "__main__":
    main()
