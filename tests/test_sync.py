"""sync.py against real temp git repos (a bare remote + working clones).

No network: the "remote" is a local bare repo, so pull/push work offline.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from handoff_mcp import sync as sync_ops


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, check=True)


def _identity(repo: Path) -> None:
    _run("git", "config", "user.email", "t@example.com", cwd=repo)
    _run("git", "config", "user.name", "Test", cwd=repo)


def _bare_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    return remote


def test_is_configured_false_for_plain_dir(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    assert sync_ops.is_configured(vault) is False


def test_ensure_gitattributes_writes_once(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    assert sync_ops.ensure_gitattributes(vault) is True
    text = (vault / ".gitattributes").read_text(encoding="utf-8")
    assert sync_ops.GITATTRIBUTES_LINE in text
    # Idempotent: a second call is a no-op and does not duplicate the line.
    assert sync_ops.ensure_gitattributes(vault) is False
    assert text.count(sync_ops.GITATTRIBUTES_LINE) == 1


def test_setup_configures_and_pushes(tmp_path: Path) -> None:
    remote = _bare_remote(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("hello\n", encoding="utf-8")

    # git identity must exist for the commit; set it after init via a pre-created repo.
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    _identity(vault)

    result = sync_ops.setup(vault, str(remote))
    assert result.status == sync_ops.STATUS_SYNCED
    assert sync_ops.is_configured(vault) is True
    assert sync_ops.GITATTRIBUTES_LINE in (vault / ".gitattributes").read_text(encoding="utf-8")
    # The remote now has our commit.
    log = subprocess.run(
        ["git", "-C", str(remote), "log", "--oneline"], capture_output=True, text=True
    )
    assert log.returncode == 0 and log.stdout.strip()


def test_setup_reports_commit_failure(tmp_path: Path) -> None:
    remote = _bare_remote(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    _identity(vault)
    # A pre-commit hook that always fails makes `git commit` return non-zero.
    hook = vault / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    (vault / "note.md").write_text("hello\n", encoding="utf-8")

    result = sync_ops.setup(vault, str(remote))
    assert result.status == sync_ops.STATUS_PUSH_FAILED
    assert "commit failed" in result.message.lower()


def _clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", str(remote), str(dest)], check=True, capture_output=True)
    _identity(dest)
    return dest


def _seed_remote_with_entity(tmp_path: Path) -> Path:
    """A bare remote whose first commit has the union .gitattributes and an
    entity note with one bullet — the baseline both clones diverge from."""

    remote = _bare_remote(tmp_path)
    seed = _clone(remote, tmp_path / "seed")
    (seed / ".gitattributes").write_text(sync_ops.GITATTRIBUTES_LINE + "\n", encoding="utf-8")
    ent = seed / "proj" / "entities"
    ent.mkdir(parents=True)
    (ent / "Architecture.md").write_text("# Architecture\n\n- baseline fact\n", encoding="utf-8")
    _run("git", "add", "-A", cwd=seed)
    _run("git", "commit", "-m", "seed", cwd=seed)
    _run("git", "push", "origin", "HEAD", cwd=seed)
    return remote


def test_sync_not_configured_returns_guidance(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    result = sync_ops.sync(vault)
    assert result.status == sync_ops.STATUS_NOT_CONFIGURED
    assert "private" in result.message.lower()


def test_two_clones_entity_append_union_merges(tmp_path: Path) -> None:
    remote = _seed_remote_with_entity(tmp_path)
    a = _clone(remote, tmp_path / "a")
    b = _clone(remote, tmp_path / "b")

    ent = "proj/entities/Architecture.md"
    # Machine A appends and pushes.
    (a / ent).write_text((a / ent).read_text() + "- fact from A\n", encoding="utf-8")
    _run("git", "add", "-A", cwd=a)
    _run("git", "commit", "-m", "A", cwd=a)
    _run("git", "push", "origin", "HEAD", cwd=a)

    # Machine B appends a DIFFERENT bullet to the same file, then syncs.
    (b / ent).write_text((b / ent).read_text() + "- fact from B\n", encoding="utf-8")
    result = sync_ops.sync(b)

    assert result.status == sync_ops.STATUS_SYNCED
    merged = (b / ent).read_text(encoding="utf-8")
    assert "fact from A" in merged and "fact from B" in merged
    assert "<<<<<<<" not in merged  # union merged, no conflict markers


def test_sync_nothing_local_still_pulls(tmp_path: Path) -> None:
    remote = _seed_remote_with_entity(tmp_path)
    b = _clone(remote, tmp_path / "b")
    result = sync_ops.sync(b)  # nothing changed locally
    assert result.status == sync_ops.STATUS_SYNCED


def test_push_failure_is_graceful(tmp_path: Path) -> None:
    remote = _seed_remote_with_entity(tmp_path)
    b = _clone(remote, tmp_path / "b")
    # Break the remote so push fails; the local commit must still be retained.
    _run("git", "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"), cwd=b)
    (b / "proj" / "entities" / "Architecture.md").write_text("# Architecture\n\n- local\n")
    result = sync_ops.sync(b)
    assert result.status == sync_ops.STATUS_PUSH_FAILED
    head = subprocess.run(["git", "-C", str(b), "log", "--oneline"], capture_output=True, text=True)
    assert "handoff: sync memory" in head.stdout


def test_sync_reports_conflict_on_unresolvable_edit(tmp_path: Path) -> None:
    # Two clones add the SAME non-entity path with different content. It is not
    # covered by the union driver (union is scoped to */entities/*.md), so the
    # rebase genuinely conflicts — exercising the STATUS_CONFLICT abort path.
    remote = _seed_remote_with_entity(tmp_path)
    a = _clone(remote, tmp_path / "a")
    b = _clone(remote, tmp_path / "b")

    (a / "notes.md").write_text("shared line A\n", encoding="utf-8")
    _run("git", "add", "-A", cwd=a)
    _run("git", "commit", "-m", "A notes", cwd=a)
    _run("git", "push", "origin", "HEAD", cwd=a)

    (b / "notes.md").write_text("shared line B\n", encoding="utf-8")
    _run("git", "add", "-A", cwd=b)
    _run("git", "commit", "-m", "B notes", cwd=b)

    result = sync_ops.sync(b)
    assert result.status == sync_ops.STATUS_CONFLICT
    # The rebase was aborted: the tree is not left mid-rebase.
    rebase_head = subprocess.run(
        ["git", "-C", str(b), "rev-parse", "--verify", "REBASE_HEAD"],
        capture_output=True,
        text=True,
    )
    assert rebase_head.returncode != 0


def test_auto_sync_flag_defaults_off_and_reads_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from handoff_mcp.config import HandoffConfig

    monkeypatch.delenv("HANDOFF_AUTO_SYNC", raising=False)
    assert HandoffConfig().auto_sync is False
    monkeypatch.setenv("HANDOFF_AUTO_SYNC", "1")
    assert HandoffConfig().auto_sync is True


def test_setup_gitignores_the_derived_index(tmp_path: Path) -> None:
    remote = _bare_remote(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    _identity(vault)
    # Simulate the engine-created index sitting in the vault.
    (vault / ".index.db").write_bytes(b"\x00sqlite")
    (vault / ".index.db-wal").write_bytes(b"")

    result = sync_ops.setup(vault, str(remote))
    assert result.status == sync_ops.STATUS_SYNCED
    tracked = subprocess.run(
        ["git", "-C", str(vault), "ls-files"], capture_output=True, text=True
    ).stdout
    assert ".index.db" not in tracked
    assert ".gitignore" in tracked


def test_git_timeout_becomes_a_failed_result(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    def _raise(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=1)

    monkeypatch.setattr(subprocess, "run", _raise)
    result = sync_ops._git(tmp_path, "status")
    assert result.returncode != 0


def test_refresh_index_reparses_new_session_file(engine) -> None:  # type: ignore[no-untyped-def]
    from handoff_mcp.models import Event, EventType, SessionMeta

    # Simulate a session file arriving out-of-band (as a git pull would deliver).
    engine.vault.ensure_session(SessionMeta(id="s_pulled_0001", project="proj-a"))
    engine.vault.append_event(
        Event(
            id="ev_pulled01",
            project="proj-a",
            session_id="s_pulled_0001",
            type=EventType.GOAL,
            content="pulled goal",
        )
    )
    engine.refresh_index()
    hits = engine.search("pulled goal", scope="current", limit=5)
    assert any(h.event.id == "ev_pulled01" for h in hits)
