#!/usr/bin/env python3
"""Smoke-gate: assert the MCP tool surface hasn't silently drifted.

Reads a ``tools/list`` response (as emitted by
``@modelcontextprotocol/inspector --cli ... --method tools/list``) on stdin and
checks the invariants the server contract promises: the set of tools, the
read-only/destructive annotations clients rely on to gate side effects, and the
enum-typed parameters. Exits non-zero with a bullet list of every mismatch so CI
fails loudly instead of shipping a broken schema.

Usage:
    ... inspector --cli ... --method tools/list | python scripts/check_tool_surface.py
"""

from __future__ import annotations

import json
import sys

EXPECTED_TOOLS = {
    "log_event",
    "get_brief",
    "search_memory",
    "checkpoint",
    "note_entity",
    "consolidate",
    "sync",
}

# (tool, annotation key, expected value) — the hints clients use to decide
# whether a tool is safe to call unprompted or needs confirmation.
EXPECTED_ANNOTATIONS = [
    ("get_brief", "readOnlyHint", True),
    ("search_memory", "readOnlyHint", True),
    ("consolidate", "destructiveHint", True),
    ("sync", "readOnlyHint", False),
    ("sync", "openWorldHint", True),
    ("sync", "idempotentHint", True),
]

# (tool, property, expected enum as a set)
EXPECTED_ENUMS = [
    ("log_event", "type", {"goal", "decision", "deadend", "file", "question", "next_step"}),
    ("search_memory", "scope", {"current", "all"}),
]


def main() -> int:
    data = json.load(sys.stdin)
    tools = {t["name"]: t for t in data.get("tools", [])}
    errors: list[str] = []

    missing = EXPECTED_TOOLS - tools.keys()
    if missing:
        errors.append(f"missing tools: {sorted(missing)}")

    for name, ann, want in EXPECTED_ANNOTATIONS:
        got = tools.get(name, {}).get("annotations", {}).get(ann)
        if got != want:
            errors.append(f"{name}.annotations.{ann}: expected {want}, got {got!r}")

    for name, prop, want in EXPECTED_ENUMS:
        props = tools.get(name, {}).get("inputSchema", {}).get("properties", {})
        got = props.get(prop, {}).get("enum")
        if got is None or set(got) != want:
            errors.append(f"{name}.{prop} enum: expected {sorted(want)}, got {got!r}")

    # search_memory grew a `limit` knob; guard it so a regression is caught.
    sm_props = tools.get("search_memory", {}).get("inputSchema", {}).get("properties", {})
    if "limit" not in sm_props:
        errors.append("search_memory: missing `limit` parameter")

    if errors:
        print("Tool-surface check FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"Tool-surface check OK ({len(tools)} tools).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
