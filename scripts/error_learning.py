#!/usr/bin/env python3
"""Capture recurring errors, categorize them, and persist lessons for MEMORY.md."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.error_learning.engine import (
    ErrorLearningEngine,
    classify_error_line,
    default_learnings_path,
    default_log_roots,
    default_memory_path,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Learn from errors: categorize log lines, persist JSON store, sync MEMORY.md.",
    )
    parser.add_argument(
        "--store",
        type=Path,
        default=None,
        help=f"JSON store path (default: {default_learnings_path()})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Read lines from stdin or --file and record errors.")
    p_ingest.add_argument("--file", type=Path, help="Optional file instead of stdin.")
    p_ingest.add_argument("--source", default="ingest", help="Label stored with each line.")
    p_ingest.add_argument("--lesson", default=None, help="Mitigation note applied to matching fingerprints.")
    p_ingest.add_argument(
        "--no-filter",
        action="store_true",
        help="Record each non-empty line even if it does not look like an error.",
    )

    p_scan = sub.add_parser("scan-logs", help="Scan OpenClaw log directories.")
    p_scan.add_argument(
        "roots",
        nargs="*",
        type=Path,
        help="Extra log roots (defaults: ~/.openclaw/logs and workspace/logs).",
    )
    p_scan.add_argument("--max-lines", type=int, default=None, help="Stop after N lines (debug).")

    p_report = sub.add_parser("report", help="Print a Markdown summary of the store.")
    p_report.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown.")

    p_sync = sub.add_parser("sync-memory", help="Append new bullets to MEMORY.md (deduplicated).")
    p_sync.add_argument("--memory-path", type=Path, default=None, help=f"Default: {default_memory_path()}")
    p_sync.add_argument("--dry-run", action="store_true", help="Show counts without writing.")

    p_classify = sub.add_parser("classify", help="Print category for a single line.")
    p_classify.add_argument("--text", required=True, help="Line to classify.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    engine = ErrorLearningEngine(store_path=args.store)

    if args.command == "classify":
        cat = classify_error_line(args.text)
        print(cat)
        return 0

    if args.command == "ingest":
        if args.file:
            text = args.file.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            src = str(args.file)
        else:
            lines = sys.stdin.read().splitlines()
            src = args.source
        for line in lines:
            engine.ingest_line(
                line,
                source=src,
                lesson=args.lesson,
                require_error_signal=not args.no_filter,
            )
        engine.save()
        print(f"Recorded {len(engine.records)} unique fingerprints in {engine.store_path}")
        return 0

    if args.command == "scan-logs":
        roots = list(args.roots) if args.roots else default_log_roots()
        n = engine.scan_logs(roots, max_lines=args.max_lines)
        engine.save()
        print(f"Processed {n} log lines; {len(engine.records)} unique fingerprints in {engine.store_path}")
        return 0

    if args.command == "report":
        if args.json:
            json.dump(engine.export_json(), sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(engine.render_report())
        return 0

    if args.command == "sync-memory":
        mem = args.memory_path or default_memory_path()
        result = engine.sync_memory(mem, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
