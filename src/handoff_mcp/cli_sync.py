"""`handoff-sync` — pull/commit/push the memory vault (multi-device sync).

    handoff-sync                    # sync the configured vault
    handoff-sync --setup <url>      # bind this vault to a private git remote

Local-only is the default mode: if you never configure a remote, memory works
fully on one device and this command just tells you how to enable sync.
"""

from __future__ import annotations

import argparse

from . import sync as sync_ops
from .config import HandoffConfig


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="handoff-sync", description=__doc__)
    parser.add_argument(
        "--setup",
        metavar="REMOTE_URL",
        help="Configure this vault for sync against REMOTE_URL (a private git repo).",
    )
    args = parser.parse_args(argv)

    vault = HandoffConfig().vault_path
    result = sync_ops.setup(vault, args.setup) if args.setup else sync_ops.sync(vault)
    print(result.message)
    if result.status != sync_ops.STATUS_SYNCED:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
