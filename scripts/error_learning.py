#!/usr/bin/env python3
"""CLI to ingest logs, categorize failures, and persist lessons (JSONL + MEMORY.md).

Typical flow:

1. Pipe CI or agent logs through ``stdin``, or pass ``--file`` / ``--glob``.
2. Structured rows append to ``~/.openclaw/errors/learned_errors.jsonl``
   (override with ``OPENCLAW_ERROR_LEARNINGS_PATH``).
3. Actionable bullets merge under ``## Error learnings`` in ``MEMORY.md``
   (see ``OPENCLAW_MEMORY_PATH``).

Optional cross-bot merge: ``--sync --bot <name>`` runs
:class:`~src.coordination.cross_bot_sync.CrossBotSyncCoordinator.sync_memory`
after updating memory.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path
from typing import cast

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.coordination.cross_bot_sync import CrossBotSyncCoordinator  # noqa: E402
from src.coordination.error_learning import (  # noqa: E402
    DEFAULT_SECTION_HEADING,
    ErrorCategory,
    extract_error_signals,
    iter_text_inputs,
    process_signals,
    register_learning,
)


def _resolve_paths(patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    for pat in patterns:
        matches = glob.glob(pat, recursive=True)
        if matches:
            out.extend(Path(m) for m in sorted(matches))
        else:
            p = Path(pat)
            if p.is_file():
                out.append(p)
    return out


def _maybe_sync(memory_path: Path, bot: str | None) -> None:
    if not bot:
        return
    coordinator = CrossBotSyncCoordinator()
    coordinator.sync_memory(bot, memory_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Learn from error logs; write JSONL and MEMORY.md lessons.",
    )
    parser.add_argument(
        "--file",
        "-f",
        action="append",
        dest="files",
        metavar="PATH",
        help="Input file (repeatable).",
    )
    parser.add_argument(
        "--glob",
        "-g",
        action="append",
        dest="globs",
        metavar="PATTERN",
        help="Glob pattern for input files (repeatable).",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read log text from standard input (in addition to files).",
    )
    parser.add_argument(
        "--memory",
        type=Path,
        default=None,
        help="MEMORY.md path (default: OPENCLAW_MEMORY_PATH or ./MEMORY.md).",
    )
    parser.add_argument(
        "--section",
        default=os.environ.get(
            "OPENCLAW_ERROR_LEARNING_SECTION", DEFAULT_SECTION_HEADING
        ),
        help=f'Memory section heading (default: "{DEFAULT_SECTION_HEADING}").',
    )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="Only append JSONL; do not modify MEMORY.md.",
    )
    parser.add_argument(
        "--no-jsonl-dedupe",
        action="store_true",
        help="Append JSONL even when fingerprint already exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report counts without writing files.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="After memory update, run cross-bot sync_memory (needs --bot).",
    )
    parser.add_argument(
        "--bot",
        default=os.environ.get("OPENCLAW_BOT_NAME"),
        help="Bot name for sync_memory (default: OPENCLAW_BOT_NAME).",
    )
    parser.add_argument(
        "--manual",
        metavar="TEXT",
        help="Register a single excerpt explicitly (no log parsing).",
    )
    parser.add_argument(
        "--category",
        default=None,
        help="With --manual, force category (optional).",
    )
    args = parser.parse_args(argv)

    memory_path = args.memory or Path(
        os.environ.get("OPENCLAW_MEMORY_PATH", str(Path.cwd() / "MEMORY.md"))
    )

    if args.manual:
        manual_category = cast(ErrorCategory | None, args.category) if args.category else None
        rec = register_learning(
            excerpt=args.manual,
            category=manual_category,
            source="cli_manual",
            memory_path=memory_path,
            section_heading=args.section,
            skip_if_seen=not args.no_jsonl_dedupe,
            write_memory=not args.no_memory,
        )
        if args.dry_run:
            print("dry-run: would register manual excerpt")
            return 0
        if rec is None:
            print("skipped (duplicate fingerprint)")
            return 0
        print(f"recorded learning fingerprint={rec.fingerprint[:12]}…")
        if args.sync and args.bot:
            _maybe_sync(memory_path, args.bot)
        return 0

    texts: list[tuple[str, str]] = []
    paths = _resolve_paths([*(args.files or []), *(args.globs or [])])
    texts.extend(iter_text_inputs(paths))
    if args.stdin or not texts:
        if args.stdin or not sys.stdin.isatty():
            payload = sys.stdin.read()
            if payload.strip():
                texts.append(("<stdin>", payload))

    if not texts:
        parser.error("No input: provide --file, --glob, or pipe to stdin.")

    total_written = 0
    total_skipped = 0
    for label, content in texts:
        signals = extract_error_signals(content, source_name=label)
        if args.dry_run:
            total_written += len(signals)
            continue
        w, s = process_signals(
            signals,
            source=label,
            memory_path=memory_path,
            section_heading=args.section,
            skip_if_seen=not args.no_jsonl_dedupe,
            write_memory=not args.no_memory,
        )
        total_written += w
        total_skipped += s

    if args.dry_run:
        print(f"dry-run: would process {total_written} signal(s) from {len(texts)} source(s)")
        return 0

    print(
        f"learned: {total_written} new, {total_skipped} skipped (duplicate), "
        f"sources={len(texts)}"
    )
    if args.sync:
        if not args.bot:
            print("warning: --sync ignored without --bot or OPENCLAW_BOT_NAME")
        else:
            _maybe_sync(memory_path, args.bot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
