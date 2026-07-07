"""Integration tests against the real MCP protocol via an in-memory client.

These exercise the actual FastMCP server end-to-end — tool discovery, argument
schemas, the resource, and the prompt — not just the engine underneath it.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session as client_session
from mcp.types import TextContent

from handoff_mcp import sync as sync_ops
from handoff_mcp.config import HandoffConfig
from handoff_mcp.engine import HandoffEngine
from handoff_mcp.server import create_server


def _server(tmp_path: Path) -> FastMCP:
    engine = HandoffEngine(
        HandoffConfig(vault_path=tmp_path / "vault", project="proj-a", session_id="s_it")
    )
    return create_server(engine)


def _text(result: object) -> str:
    block = result.content[0]  # type: ignore[attr-defined]
    assert isinstance(block, TextContent)
    return block.text


def test_package_version_comes_from_distribution_metadata() -> None:
    from importlib.metadata import version

    from handoff_mcp import __version__

    assert __version__ == version("handoff-mcp")


async def test_tools_are_exposed(tmp_path: Path) -> None:
    async with client_session(_server(tmp_path)) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
    assert {"log_event", "get_brief", "search_memory", "checkpoint", "note_entity"} <= tools


async def test_tool_annotations_are_published(tmp_path: Path) -> None:
    async with client_session(_server(tmp_path)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    assert tools["get_brief"].annotations is not None
    assert tools["get_brief"].annotations.readOnlyHint is True
    assert tools["search_memory"].annotations is not None
    assert tools["search_memory"].annotations.readOnlyHint is True
    assert tools["log_event"].annotations is not None
    assert tools["log_event"].annotations.destructiveHint is False
    assert tools["log_event"].annotations.idempotentHint is False
    assert tools["consolidate"].annotations is not None
    assert tools["consolidate"].annotations.destructiveHint is True
    assert tools["consolidate"].annotations.openWorldHint is True
    # Everything except consolidate and sync stays inside the local vault.
    for name, tool in tools.items():
        if name in ("consolidate", "sync"):
            continue
        assert tool.annotations is not None
        assert tool.annotations.openWorldHint is False, name


async def test_event_type_schema_is_enum_constrained(tmp_path: Path) -> None:
    async with client_session(_server(tmp_path)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    type_schema = tools["log_event"].inputSchema["properties"]["type"]
    assert set(type_schema["enum"]) == {
        "goal",
        "decision",
        "deadend",
        "file",
        "question",
        "next_step",
    }
    scope_schema = tools["search_memory"].inputSchema["properties"]["scope"]
    assert set(scope_schema["enum"]) == {"current", "all"}


async def test_invalid_event_type_is_rejected(tmp_path: Path) -> None:
    async with client_session(_server(tmp_path)) as client:
        result = await client.call_tool("log_event", {"type": "musing", "content": "x"})
    assert result.isError


async def test_misspelled_scope_is_rejected_not_coerced(tmp_path: Path) -> None:
    # A typo like 'curent' used to silently fall back to searching everything.
    async with client_session(_server(tmp_path)) as client:
        result = await client.call_tool("search_memory", {"query": "x", "scope": "curent"})
    assert result.isError


async def test_search_returns_event_ids_and_respects_limit(tmp_path: Path) -> None:
    async with client_session(_server(tmp_path)) as client:
        eid = _text(
            await client.call_tool(
                "log_event",
                {"type": "decision", "content": "Chunk requests to avoid gateway timeouts"},
            )
        )
        await client.call_tool(
            "log_event", {"type": "decision", "content": "Retry chunk uploads on timeout"}
        )
        out = _text(await client.call_tool("search_memory", {"query": "chunk timeout"}))
        limited = _text(
            await client.call_tool("search_memory", {"query": "chunk timeout", "limit": 1})
        )

    assert f"(id: {eid})" in out  # id is exposed, closing the supersession loop
    assert "Found 1 result(s)" in limited


async def test_log_then_brief_roundtrip(tmp_path: Path) -> None:
    async with client_session(_server(tmp_path)) as client:
        await client.call_tool(
            "log_event",
            {"type": "goal", "content": "Ship feature X", "importance": 5},
        )
        await client.call_tool(
            "log_event",
            {"type": "next_step", "content": "Write the parser"},
        )
        brief = _text(await client.call_tool("get_brief", {}))

    assert "Ship feature X" in brief
    assert "Write the parser" in brief


async def test_supersession_over_protocol(tmp_path: Path) -> None:
    async with client_session(_server(tmp_path)) as client:
        old = _text(
            await client.call_tool("log_event", {"type": "decision", "content": "Use JSON storage"})
        )
        await client.call_tool(
            "log_event",
            {"type": "decision", "content": "Use SQLite storage", "supersedes": [old]},
        )
        brief = _text(await client.call_tool("get_brief", {}))

    assert "SQLite" in brief
    assert "JSON" not in brief


async def test_supersedes_query_over_protocol(tmp_path: Path) -> None:
    async with client_session(_server(tmp_path)) as client:
        await client.call_tool(
            "log_event", {"type": "decision", "content": "Persist data in flat JSON files"}
        )
        out = _text(
            await client.call_tool(
                "log_event",
                {
                    "type": "decision",
                    "content": "Persist data in SQLite instead",
                    "supersedes_query": "persist data files",
                },
            )
        )
        brief = _text(await client.call_tool("get_brief", {}))

    assert "Superseded by best match" in out  # audit line names the retired event
    assert "SQLite" in brief
    assert "JSON" not in brief


async def test_supersedes_query_no_match_is_reported(tmp_path: Path) -> None:
    async with client_session(_server(tmp_path)) as client:
        out = _text(
            await client.call_tool(
                "log_event",
                {
                    "type": "decision",
                    "content": "A standalone decision",
                    "supersedes_query": "nothing like this exists xyzzy",
                },
            )
        )
    assert "no active decision matched" in out


async def test_cross_project_search(tmp_path: Path) -> None:
    async with client_session(_server(tmp_path)) as client:
        await client.call_tool(
            "log_event",
            {
                "type": "decision",
                "content": "Chunk requests to avoid timeouts",
                "project": "hermes",
            },
        )
        out = _text(
            await client.call_tool("search_memory", {"query": "timeout chunk", "scope": "all"})
        )

    assert "hermes" in out
    assert "Chunk requests" in out


async def test_resource_and_prompt(tmp_path: Path) -> None:
    from pydantic import AnyUrl

    async with client_session(_server(tmp_path)) as client:
        await client.call_tool("log_event", {"type": "goal", "content": "Resource goal"})

        res = await client.read_resource(AnyUrl("session://brief"))
        assert "Resource goal" in res.contents[0].text  # type: ignore[union-attr]

        prompt = await client.get_prompt("resume")
        joined = " ".join(
            m.content.text for m in prompt.messages if isinstance(m.content, TextContent)
        )
        assert "Resource goal" in joined
        assert "do not redo settled decisions" in joined


async def test_sync_tool_exposed_with_annotations(tmp_path: Path) -> None:
    async with client_session(_server(tmp_path)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert "sync" in tools
    ann = tools["sync"].annotations
    assert ann is not None
    assert ann.readOnlyHint is False
    assert ann.openWorldHint is True
    assert ann.idempotentHint is True


async def test_sync_tool_on_plain_vault_returns_setup_guidance(tmp_path: Path) -> None:
    async with client_session(_server(tmp_path)) as client:
        result = await client.call_tool("sync", {})
    assert "private" in _text(result).lower()


async def test_auto_sync_off_does_not_touch_git(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # With auto-sync off (default), get_brief/checkpoint must not invoke sync.
    calls: list[str] = []
    monkeypatch.setattr("handoff_mcp.server.sync_ops.pull", lambda v: calls.append("pull"))
    monkeypatch.setattr("handoff_mcp.server.sync_ops.sync", lambda v: calls.append("sync"))
    async with client_session(_server(tmp_path)) as client:
        await client.call_tool("get_brief", {})
        await client.call_tool("checkpoint", {})
    assert calls == []


async def test_auto_sync_on_pulls_on_brief_and_pushes_on_checkpoint(  # type: ignore[no-untyped-def]
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HANDOFF_AUTO_SYNC", "1")
    calls: list[str] = []

    def _fake_pull(vault: object) -> sync_ops.SyncResult:
        calls.append("pull")
        return sync_ops.SyncResult(sync_ops.STATUS_SYNCED, "ok")

    def _fake_sync(vault: object) -> sync_ops.SyncResult:
        calls.append("sync")
        return sync_ops.SyncResult(sync_ops.STATUS_SYNCED, "ok")

    monkeypatch.setattr("handoff_mcp.server.sync_ops.pull", _fake_pull)
    monkeypatch.setattr("handoff_mcp.server.sync_ops.sync", _fake_sync)
    async with client_session(_server(tmp_path)) as client:
        await client.call_tool("get_brief", {})
        await client.call_tool("checkpoint", {})
    assert "pull" in calls and "sync" in calls
