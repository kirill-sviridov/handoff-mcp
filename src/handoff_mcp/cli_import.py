"""`handoff-import` — bootstrap a project's memory from existing sources.

    handoff-import git <repo-path> --project NAME
    handoff-import claude <transcript.jsonl> --project NAME

Writes into the same shared vault as the server (honours HANDOFF_VAULT). Import
is idempotent — re-running the same source adds nothing new.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from .config import HandoffConfig
from .engine import HandoffEngine
from .importers import GIT_LOG_FORMAT, events_from_claude_transcript, events_from_git_log


def _git_log(repo: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-C",
            repo,
            "log",
            "--no-merges",
            "--name-only",
            f"--pretty=format:{GIT_LOG_FORMAT}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="handoff-import", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    git_cmd = sub.add_parser("git", help="import a repo's commit history")
    git_cmd.add_argument("repo", help="path to the git repository")
    git_cmd.add_argument("--project", required=True, help="project namespace to import into")

    claude_cmd = sub.add_parser("claude", help="import a Claude Code transcript (JSONL)")
    claude_cmd.add_argument("transcript", help="path to the .jsonl transcript")
    claude_cmd.add_argument("--project", required=True, help="project namespace to import into")

    args = parser.parse_args(argv)

    engine = HandoffEngine(HandoffConfig(project=args.project))
    try:
        if args.cmd == "git":
            events = events_from_git_log(_git_log(args.repo), project=args.project)
        else:
            text = Path(args.transcript).read_text(encoding="utf-8")
            events = events_from_claude_transcript(text, project=args.project)
        imported = engine.ingest_events(events)
        print(f"Imported {imported} new event(s) into project '{args.project}'.")
    finally:
        engine.close()


if __name__ == "__main__":
    main()
