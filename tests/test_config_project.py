"""Project-namespace derivation: env > git root > cwd basename > default."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from handoff_mcp.config import ENV_PROJECT, HandoffConfig, _derive_project_from_cwd


@pytest.fixture(autouse=True)
def _no_env_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_PROJECT, raising=False)


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def test_env_var_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_PROJECT, "explicit-env")
    monkeypatch.chdir(tmp_path)
    assert HandoffConfig(vault_path=tmp_path / "v").project == "explicit-env"


def test_explicit_arg_wins_over_everything(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_PROJECT, "explicit-env")
    monkeypatch.chdir(tmp_path)
    assert HandoffConfig(vault_path=tmp_path / "v", project="arg").project == "arg"


def test_git_repo_uses_toplevel_basename(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "my-repo"
    repo.mkdir()
    _git_init(repo)
    sub = repo / "deep" / "inside"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert _derive_project_from_cwd() == "my-repo"


def test_non_git_dir_uses_cwd_basename(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    d = tmp_path / "plain-dir"
    d.mkdir()
    monkeypatch.chdir(d)
    assert _derive_project_from_cwd() == "plain-dir"


def test_home_cwd_falls_back_to_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.chdir(tmp_path)
    assert _derive_project_from_cwd() == "default"


def test_git_repo_at_home_falls_back_to_cwd_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # dotfiles repo at $HOME must not leak the username; a subdir of it is
    # not home itself, so the cwd basename is used instead of the toplevel.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _git_init(tmp_path)
    sub = tmp_path / "notes"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert _derive_project_from_cwd() == "notes"


def test_whitespace_in_name_sanitised(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    d = tmp_path / "my project"
    d.mkdir()
    monkeypatch.chdir(d)
    assert _derive_project_from_cwd() == "my-project"


@pytest.mark.skipif(sys.platform == "win32", reason="cwd cannot be removed while in use on Windows")
def test_deleted_cwd_falls_back_to_default(tmp_path: Path) -> None:
    doomed = tmp_path / "doomed"
    doomed.mkdir()
    os.chdir(doomed)
    doomed.rmdir()
    try:
        assert _derive_project_from_cwd() == "default"
    finally:
        os.chdir(tmp_path)


def test_git_timeout_falls_back_to_cwd_basename(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    d = tmp_path / "timeout-dir"
    d.mkdir()
    monkeypatch.chdir(d)

    def _boom(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="git", timeout=2.0)

    monkeypatch.setattr("handoff_mcp.config.subprocess.run", _boom)
    assert _derive_project_from_cwd() == "timeout-dir"
