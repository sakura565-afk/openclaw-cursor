#!/usr/bin/env python3
"""Kara cron helper: drain Iskra shared-memory queue, else fall back to tasks/results.

When there is nothing to forward, prints ``NO_REPLY`` (same contract as the
legacy file-scan cron). Intended for OpenClaw cron payload ``message`` that
runs this script from the repo root, for example:

    python -m scripts.kara_poll_iskra_results

Cron reference (update payload in OpenClaw UI): ID ``646f9a49-8aed-4521-9e28-841f9366156b``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from src.coordination.iskra_kara_shared_memory import (
    collect_fallback_tasks_results,
    commit_fallback_consumed,
    default_results_path,
    drain_shared_memory_entries,
    format_kara_message,
    resolve_openclaw_workspace,
)


NO_REPLY = "NO_REPLY"


def poll_once(
    *,
    workspace: Path,
    results_path: Path,
    use_fallback: bool,
) -> Tuple[List[Dict[str, Any]], str]:
    """Returns (entries, source) where source is ``shared``, ``fallback``, or ``none``."""

    entries, status = drain_shared_memory_entries(results_path=results_path)

    if entries:
        return entries, "shared"

    if use_fallback and status in ("corrupt", "inaccessible"):
        fb_entries, _ = collect_fallback_tasks_results(workspace)
        if fb_entries:
            return fb_entries, "fallback"

    return [], "none"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drain Iskra→Kara shared queue or fall back to tasks/results.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="OpenClaw workspace root (default: OPENCLAW_WORKSPACE or ~/.openclaw/workspace).",
    )
    parser.add_argument(
        "--results-path",
        type=Path,
        default=None,
        help="Override path to iskra_kara_results.json.",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Do not read tasks/results when the queue is corrupt or inaccessible.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (meta + entries) instead of markdown (for tooling/tests).",
    )
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else resolve_openclaw_workspace()
    results_path = (
        Path(args.results_path).expanduser().resolve()
        if args.results_path
        else default_results_path(workspace)
    )
    use_fallback = not args.no_fallback

    entries, source = poll_once(workspace=workspace, results_path=results_path, use_fallback=use_fallback)

    if not entries:
        print(NO_REPLY)
        return 0

    if args.json:
        payload = {
            "source": source,
            "count": len(entries),
            "entries": entries,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_kara_message(entries), end="")

    if source == "fallback":
        commit_fallback_consumed(entries, workspace)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
