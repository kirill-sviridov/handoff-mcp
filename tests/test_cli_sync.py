"""handoff-sync CLI smoke over a temp vault."""

from __future__ import annotations

from pathlib import Path

import pytest

from handoff_mcp import cli_sync


def test_plain_sync_on_unconfigured_vault_prints_guidance_and_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HANDOFF_VAULT", str(tmp_path / "vault"))
    with pytest.raises(SystemExit) as exc:
        cli_sync.main([])
    assert exc.value.code == 1
    assert "private" in capsys.readouterr().out.lower()


def test_setup_configures_vault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import subprocess

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", "T"], check=True)
    monkeypatch.setenv("HANDOFF_VAULT", str(vault))

    cli_sync.main(["--setup", str(remote)])
    out = capsys.readouterr().out.lower()
    assert "configured" in out
