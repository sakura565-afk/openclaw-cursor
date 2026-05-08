#!/usr/bin/env python3
"""Capture recurring errors from logs, categorize them, persist deduped learnings, and sync MEMORY.md."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.error_learning.engine import ErrorLearningEngine
from src.error_learning.memory_bridge import format_learning_bullet
from src.error_learning.taxonomy import classify_error_text


def _parse_memory_path(arg: str | None) -> Path | None:
    if arg:
        return Path(arg).expanduser()
    env = os.environ.get("OPENCLAW_MEMORY_PATH")
    if env:
        return Path(env).expanduser()
    return None


def cmd_scan(engine: ErrorLearningEngine, args: argparse.Namespace) -> int:
    engine.scan_logs()
    return cmd_report(engine, args)


def cmd_ingest(engine: ErrorLearningEngine, args: argparse.Namespace) -> int:
    lines: list[str] = []
    if args.file:
        path = Path(args.file)
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
    else:
        if sys.stdin.isatty() and not args.message:
            print("Provide --file, --message, or pipe text on stdin.", file=sys.stderr)
            return 2
        if args.message:
            lines = args.message.splitlines()
        else:
            lines = sys.stdin.read().splitlines()
    recorded = engine.ingest_lines(lines, source=args.source)
    print(f"Recorded {len(recorded)} error signal(s).")
    return 0


def cmd_report(engine: ErrorLearningEngine, args: argparse.Namespace) -> int:
    rows = engine.observations()
    if getattr(args, "json", False):
        payload = {
            "store": str(engine.store_path),
            "summary": engine.summary(),
            "observations": [o.to_dict() for o in rows],
        }
        print(json.dumps(payload, indent=2))
        return 0
    print(f"Store: {engine.store_path}")
    print("Summary by category (total weighted counts):")
    for cat, n in engine.summary().items():
        print(f"  {cat}: {n}")
    print(f"\nObservations ({len(rows)}):")
    limit = getattr(args, "limit", 50)
    for obs in rows[:limit]:
        print(format_learning_bullet(obs))
    if len(rows) > limit:
        print(f"... ({len(rows) - limit} more)")
    return 0


def cmd_classify(_engine: ErrorLearningEngine, args: argparse.Namespace) -> int:
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    if not text:
        print("Provide --text or --file.", file=sys.stderr)
        return 2
    result = classify_error_text(text)
    out = {"category": result.category.value, "signals": list(result.signals)}
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"category={result.category.value} signals={list(result.signals)}")
    return 0


def cmd_sync_memory(engine: ErrorLearningEngine, args: argparse.Namespace) -> int:
    path = engine.sync_memory(_parse_memory_path(args.memory_path), max_entries=args.max_entries)
    print(f"Updated memory file: {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw error learning pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Scan default log roots and update the store")
    p_scan.add_argument("--json", action="store_true", help="Machine-readable output")
    p_scan.add_argument("--limit", type=int, default=50, help="Max lines in text mode")
    p_scan.set_defaults(func=cmd_scan)

    p_ingest = sub.add_parser("ingest", help="Ingest lines from stdin, --file, or --message")
    p_ingest.add_argument("--file", "-f", help="Path to a text file")
    p_ingest.add_argument("--message", "-m", help="Literal message (can be multiline)")
    p_ingest.add_argument("--source", help="Optional source label stored with counts")
    p_ingest.set_defaults(func=cmd_ingest)

    p_rep = sub.add_parser("report", help="Print observations from the store")
    p_rep.add_argument("--json", action="store_true", help="Machine-readable output")
    p_rep.add_argument("--limit", type=int, default=50, help="Max lines in text mode")
    p_rep.set_defaults(func=cmd_report)

    p_cls = sub.add_parser("classify", help="Classify a single message without storing")
    p_cls.add_argument("--text", "-t", default="", help="Error text")
    p_cls.add_argument("--file", help="Read text from file")
    p_cls.add_argument("--json", action="store_true")
    p_cls.set_defaults(func=cmd_classify)

    p_mem = sub.add_parser("sync-memory", help="Merge top observations into MEMORY.md")
    p_mem.add_argument("--memory-path", help="Override OPENCLAW_MEMORY_PATH")
    p_mem.add_argument("--max-entries", type=int, default=40)
    p_mem.set_defaults(func=cmd_sync_memory)

    for subparser in (p_scan, p_ingest, p_rep, p_cls, p_mem):
        subparser.add_argument(
            "--root-dir",
            default=".",
            help="Repository root (default logs/error_learning_store.json unless overridden)",
        )
        subparser.add_argument(
            "--store",
            help="Override OPENCLAW_ERROR_LEARNING_STORE path",
        )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store_path = Path(args.store).expanduser() if getattr(args, "store", None) else None
    engine = ErrorLearningEngine(root_dir=Path(args.root_dir), store_path=store_path)
    func = args.func
    return func(engine, args)


if __name__ == "__main__":
    raise SystemExit(main())
