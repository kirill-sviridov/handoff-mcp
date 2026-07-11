"""Claude Code plugin manifest: valid JSON, version-synced, files present."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

try:  # tomllib is 3.11+; CI matrix includes 3.10
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent


def _load(rel: str) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads((ROOT / rel).read_text(encoding="utf-8")))


def test_plugin_version_matches_pyproject() -> None:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        py_version = tomllib.load(fh)["project"]["version"]
    assert _load(".claude-plugin/plugin.json")["version"] == py_version


def test_server_json_versions_match_pyproject() -> None:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        py_version = tomllib.load(fh)["project"]["version"]
    server = _load("server.json")
    assert server["version"] == py_version
    assert server["packages"][0]["version"] == py_version


def test_plugin_name_is_kebab_case() -> None:
    name = _load(".claude-plugin/plugin.json")["name"]
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name)


def test_plugin_referenced_files_exist() -> None:
    manifest = _load(".claude-plugin/plugin.json")
    for key in ("mcpServers", "hooks", "skills"):
        assert (ROOT / manifest[key]).exists(), f"{key} → {manifest[key]} missing"


def test_marketplace_lists_this_plugin() -> None:
    market = _load(".claude-plugin/marketplace.json")
    [entry] = market["plugins"]
    assert entry["name"] == _load(".claude-plugin/plugin.json")["name"]
    assert entry["source"] == "./"


def test_mcp_config_uses_uvx_and_handoff_key() -> None:
    servers = _load("mcp-config.json")["mcpServers"]
    assert list(servers) == ["handoff"]
    assert servers["handoff"]["command"] == "uvx"
    assert servers["handoff"]["args"] == ["handoff-mcp"]
    assert servers["handoff"]["env"] == {}


def test_hook_prints_protocol_file() -> None:
    hooks = _load("hooks/hooks.json")["hooks"]
    [start] = hooks["SessionStart"]
    [cmd] = start["hooks"]
    assert cmd["type"] == "command"
    assert "protocol.md" in cmd["command"]
    assert (ROOT / "hooks" / "protocol.md").exists()
