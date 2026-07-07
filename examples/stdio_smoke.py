"""Launch handoff-mcp as a real stdio MCP server and talk to it over the wire.

Unlike the in-memory integration tests, this spawns the server as a separate
process exactly as Claude Code / Claude Desktop would, performs the MCP
handshake, calls the tools, and reads the resource and prompt. It's the proof
that the packaged server actually runs end-to-end.

    python examples/stdio_smoke.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    vault = tempfile.mkdtemp()
    env = dict(os.environ)
    env.update(HANDOFF_VAULT=vault, HANDOFF_PROJECT="smoke", HANDOFF_SEMANTIC="1")

    # Equivalent to the `handoff-mcp` console script; using -m avoids PATH issues.
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "handoff_mcp.server"], env=env
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        print(f"connected to {init.serverInfo.name} v{init.serverInfo.version}")

        tools = sorted(t.name for t in (await session.list_tools()).tools)
        print("tools:", tools)

        await session.call_tool("log_event", {"type": "goal", "content": "Prove stdio works"})
        await session.call_tool(
            "log_event", {"type": "next_step", "content": "Ship it. See [[Roadmap]]."}
        )
        await session.call_tool("note_entity", {"name": "Roadmap", "content": "v0.1 then PyPI."})

        brief = await session.call_tool("get_brief", {})
        print("\n--- brief ---")
        print(brief.content[0].text)  # type: ignore[union-attr]

        resources = [str(r.uri) for r in (await session.list_resources()).resources]
        prompts = [p.name for p in (await session.list_prompts()).prompts]
        print("resources:", resources, "| prompts:", prompts)
        print("\nstdio smoke test OK")


if __name__ == "__main__":
    asyncio.run(main())
