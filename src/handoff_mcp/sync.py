"""Thin git layer over the vault — the only place git is invoked.

The vault is already a git repo in a multi-device setup; this module turns
pull/commit/push into typed, testable operations. Every operation degrades
gracefully: an unconfigured, offline, or conflicted vault yields a SyncResult,
never an exception, so callers (the sync tool, the CLI, the auto-triggers) treat
sync as best-effort and never block a session.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Committed to the vault so it reaches every machine: git's built-in `union`
# merge driver concatenates both sides of a conflicting entity-note tail instead
# of raising a conflict. Entity notes are append-only bullet lists — union fits.
GITATTRIBUTES_LINE = "*/entities/*.md merge=union"

# The derived SQLite index (and its WAL/SHM sidecars) is rebuilt from the
# markdown on startup and must never be committed — it would collide as a binary
# file on every pull. setup()/sync() ensure the vault ignores it.
GITIGNORE_LINE = ".index.db*"

# Git network ops must never hang a session: cap them and refuse interactive
# credential prompts (a timeout is surfaced as a failed CompletedProcess).
GIT_TIMEOUT_SECONDS = 30

STATUS_SYNCED = "synced"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_CONFLICT = "conflict"
STATUS_PUSH_FAILED = "push_failed"

SETUP_GUIDANCE = (
    "This vault isn't set up for sync yet. Ask the user for a PRIVATE git repo "
    "URL (they can create one, e.g. `gh repo create <name> --private`), then "
    "configure it: `handoff-sync --setup <url>` or call the sync tool with "
    "remote_url set to that URL."
)


@dataclass
class SyncResult:
    """Outcome of a sync operation, with a human-readable, actionable message."""

    status: str
    message: str


def _git(vault: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in the vault, capturing output. Never raises and never
    blocks: interactive credential prompts are disabled and the call is bounded
    by GIT_TIMEOUT_SECONDS; a timeout is returned as a failed result."""

    try:
        return subprocess.run(
            ["git", "-C", str(vault), *args],
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            ["git", *args], returncode=124, stdout="", stderr="git timed out"
        )


def is_git_repo(vault: Path) -> bool:
    return _git(vault, "rev-parse", "--is-inside-work-tree").returncode == 0


def has_remote(vault: Path) -> bool:
    result = _git(vault, "remote")
    return result.returncode == 0 and bool(result.stdout.strip())


def is_configured(vault: Path) -> bool:
    """Sync-ready = a git repo with at least one remote."""

    return is_git_repo(vault) and has_remote(vault)


def ensure_gitattributes(vault: Path) -> bool:
    """Ensure .gitattributes carries the union-merge line for entity notes.
    Returns True if it wrote the line, False if it was already present."""

    path = vault / ".gitattributes"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if GITATTRIBUTES_LINE in existing:
        return False
    prefix = existing.rstrip() + "\n" if existing.strip() else ""
    path.write_text(prefix + GITATTRIBUTES_LINE + "\n", encoding="utf-8")
    return True


def ensure_gitignore(vault: Path) -> bool:
    """Ensure the vault's .gitignore excludes the derived SQLite index. Returns
    True if it wrote the line, False if the index is already ignored."""

    path = vault / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    # Respect an existing entry (the user's real vault lists .index.db explicitly).
    if ".index.db" in existing:
        return False
    prefix = existing.rstrip() + "\n" if existing.strip() else ""
    path.write_text(prefix + GITIGNORE_LINE + "\n", encoding="utf-8")
    return True


def setup(vault: Path, remote_url: str) -> SyncResult:
    """Configure the vault for sync: git init if needed, point `origin` at
    remote_url, write the union .gitattributes, make an initial commit, push.
    Idempotent."""

    vault.mkdir(parents=True, exist_ok=True)
    if not is_git_repo(vault):
        init = _git(vault, "init")
        if init.returncode != 0:
            return SyncResult(STATUS_NOT_CONFIGURED, f"git init failed: {init.stderr.strip()}")
    if has_remote(vault):
        _git(vault, "remote", "set-url", "origin", remote_url)
    else:
        _git(vault, "remote", "add", "origin", remote_url)
    ensure_gitattributes(vault)
    ensure_gitignore(vault)
    _git(vault, "add", "-A")
    commit = _git(vault, "commit", "-m", "handoff: configure vault sync")
    # A non-zero commit is benign only when there was nothing to commit; any
    # other failure (hook, disk, identity) must not be masked by pushing old HEAD.
    if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
        return SyncResult(
            STATUS_PUSH_FAILED,
            f"Vault configured, but the commit failed: {commit.stderr.strip()}",
        )
    push = _git(vault, "push", "-u", "origin", "HEAD")
    if push.returncode != 0:
        return SyncResult(
            STATUS_PUSH_FAILED,
            "Vault configured locally, but the initial push failed — check git "
            f"auth (e.g. `gh auth login` or an SSH key). git said: {push.stderr.strip()}",
        )
    return SyncResult(STATUS_SYNCED, "Vault configured for sync and pushed to origin.")


def _current_branch(vault: Path) -> str:
    branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    return branch or "HEAD"


def pull(vault: Path) -> SyncResult:
    """git pull --rebase --autostash. A genuine conflict (one union can't
    resolve) aborts the rebase to restore a clean tree and reports a manual fix.
    An unreachable remote is treated as offline (SYNCED), not a conflict."""

    if not is_configured(vault):
        return SyncResult(STATUS_NOT_CONFIGURED, SETUP_GUIDANCE)
    result = _git(vault, "pull", "--rebase", "--autostash", "origin", _current_branch(vault))
    if result.returncode == 0:
        return SyncResult(STATUS_SYNCED, "Pulled latest from origin.")
    # Distinguish a merge conflict (mid-rebase) from an offline/remote error.
    in_rebase = _git(vault, "rev-parse", "--verify", "REBASE_HEAD").returncode == 0
    if in_rebase:
        _git(vault, "rebase", "--abort")
        return SyncResult(
            STATUS_CONFLICT,
            "Pull hit a conflict git couldn't auto-merge (likely the same existing "
            f"line edited on two machines). The vault is unchanged; resolve by hand "
            f"in {vault}.",
        )
    # Offline / no upstream yet: nothing to pull, proceed.
    return SyncResult(STATUS_SYNCED, "Nothing pulled (offline or no upstream).")


def sync(vault: Path) -> SyncResult:
    """Full sync: pull --rebase, then commit any local changes and push."""

    if not is_configured(vault):
        return SyncResult(STATUS_NOT_CONFIGURED, SETUP_GUIDANCE)
    ensure_gitattributes(vault)
    ensure_gitignore(vault)
    pulled = pull(vault)
    if pulled.status == STATUS_CONFLICT:
        return pulled
    _git(vault, "add", "-A")
    if not _git(vault, "status", "--porcelain").stdout.strip():
        return SyncResult(STATUS_SYNCED, "Up to date (pulled; nothing local to push).")
    # There ARE staged changes here (porcelain was non-empty), so a non-zero
    # commit is a genuine failure (hook, disk, identity), not "nothing to commit".
    commit = _git(vault, "commit", "-m", "handoff: sync memory")
    if commit.returncode != 0:
        return SyncResult(
            STATUS_PUSH_FAILED,
            f"Commit failed (nothing reached the remote): {commit.stderr.strip()}",
        )
    push = _git(vault, "push", "origin", "HEAD")
    if push.returncode != 0:
        return SyncResult(
            STATUS_PUSH_FAILED,
            "Committed locally but push failed (offline or auth). It will push on "
            f"the next sync. git said: {push.stderr.strip()}",
        )
    return SyncResult(STATUS_SYNCED, "Synced: pulled, committed, and pushed.")
