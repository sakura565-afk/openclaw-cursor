#!/usr/bin/env python3
"""Capture and learn from recurring OpenClaw session errors."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final, Iterator


ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_ERRORS_DIR: Final = ROOT / ".learnings" / "errors"
SCHEMA_VERSION: Final = 1

ANSI: Final[dict[str, str]] = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}


class ErrorLearningError(RuntimeError):
    """Raised when error learnings cannot be read or written safely."""


@dataclass(frozen=True)
class PatternHistory:
    """Result of looking up whether an error fingerprint appeared before."""

    seen: bool
    """True if at least one prior entry matches the fingerprint."""
    timestamps_iso: tuple[str, ...]
    """All matching occurrence timestamps, oldest first."""
    first_seen_iso: str | None
    last_seen_iso: str | None


def colorize(text: str, color: str) -> str:
    """Wrap text in ANSI color codes unless NO_COLOR is set."""

    if os.environ.get("NO_COLOR"):
        return text
    prefix = ANSI.get(color, "")
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI['reset']}"


def utc_now_iso() -> str:
    """Current UTC time as ISO-8601 with Z suffix."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_component(value: str | None) -> str:
    """Normalize path or name for stable fingerprinting."""

    if value is None:
        return ""
    return value.strip()


def fingerprint(error_type: str, file: str | None, function: str | None) -> str:
    """Deduplication key: error type + file + function name."""

    payload = {
        "error_type": normalize_component(error_type).lower(),
        "file": normalize_component(file),
        "function": normalize_component(function),
    }
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return digest


def _entry_fingerprint(entry: dict[str, Any]) -> str:
    return fingerprint(
        str(entry.get("error_type", "")),
        str(entry.get("file")) if entry.get("file") is not None else None,
        str(entry.get("function")) if entry.get("function") is not None else None,
    )


def build_entry(
    error_type: str,
    message: str,
    *,
    file: str | None = None,
    function: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Create a single error log entry dict."""

    ts = timestamp or utc_now_iso()
    fp = fingerprint(error_type, file, function)
    digest = hashlib.sha1(
        json.dumps(
            {"error_type": error_type, "message": message, "file": file, "function": function, "ts": ts},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:12]
    entry: dict[str, Any] = {
        "id": digest,
        "fingerprint": fp,
        "timestamp": ts,
        "error_type": normalize_component(error_type) or "(unknown)",
        "message": message.strip(),
    }
    if file is not None and normalize_component(file):
        entry["file"] = normalize_component(file)
    if function is not None and normalize_component(function):
        entry["function"] = normalize_component(function)
    return entry


def default_day_document(day: date) -> dict[str, Any]:
    """Empty on-disk document for one calendar day."""

    return {
        "schema_version": SCHEMA_VERSION,
        "date": day.isoformat(),
        "entries": [],
    }


def day_file_path(errors_dir: Path, day: date) -> Path:
    """JSON path for a given UTC calendar day."""

    return errors_dir / f"{day.isoformat()}.json"


def validate_entry(raw: object) -> dict[str, Any]:
    """Validate one persisted entry."""

    if not isinstance(raw, dict):
        raise ErrorLearningError("Each entry must be a JSON object.")
    entry = dict(raw)
    for key in ("timestamp", "error_type", "message"):
        val = entry.get(key)
        if not isinstance(val, str) or not val.strip():
            raise ErrorLearningError(f"Entry field '{key}' must be a non-empty string.")
    entry["fingerprint"] = fingerprint(
        str(entry["error_type"]),
        str(entry["file"]) if entry.get("file") is not None else None,
        str(entry["function"]) if entry.get("function") is not None else None,
    )
    eid = entry.get("id")
    if not isinstance(eid, str) or not eid.strip():
        entry["id"] = hashlib.sha1(
            json.dumps(
                {
                    "error_type": entry["error_type"],
                    "message": entry["message"],
                    "file": entry.get("file"),
                    "function": entry.get("function"),
                    "timestamp": entry["timestamp"],
                },
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:12]
    return entry


def _load_day_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErrorLearningError(f"Invalid JSON in {path}: {exc}") from exc
    if isinstance(raw, list):
        day_str = path.stem
        try:
            parsed = date.fromisoformat(day_str)
        except ValueError:
            parsed = datetime.now(timezone.utc).date()
        validated_list = [validate_entry(item) for item in raw]
        return {"schema_version": SCHEMA_VERSION, "date": parsed.isoformat(), "entries": validated_list}
    if not isinstance(raw, dict):
        raise ErrorLearningError(f"Expected object or list in {path}.")
    entries = raw.get("entries", [])
    if not isinstance(entries, list):
        raise ErrorLearningError(f"Field 'entries' must be a list in {path}.")
    raw["entries"] = [validate_entry(item) for item in entries]
    return raw


def iter_day_files(errors_dir: Path) -> Iterator[Path]:
    """Yield existing day JSON paths sorted by date ascending."""

    if not errors_dir.is_dir():
        return
    paths = sorted(errors_dir.glob("*.json"), key=lambda p: p.name)
    for path in paths:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", path.name):
            yield path


def load_all_entries(errors_dir: Path) -> list[dict[str, Any]]:
    """Load and validate every entry from all day files."""

    out: list[dict[str, Any]] = []
    if not errors_dir.exists():
        return out
    for path in iter_day_files(errors_dir):
        try:
            doc = _load_day_file(path)
        except ErrorLearningError:
            continue
        if doc is None:
            continue
        entries = doc.get("entries", [])
        if isinstance(entries, list):
            out.extend(entries)
    out.sort(key=lambda e: str(e["timestamp"]))
    return out


def pattern_seen_before(
    errors_dir: Path,
    error_type: str,
    *,
    file: str | None = None,
    function: str | None = None,
) -> PatternHistory:
    """Return whether this error fingerprint exists in stored learnings and when."""

    target = fingerprint(error_type, file, function)
    matches: list[str] = []
    for entry in load_all_entries(errors_dir):
        if str(entry.get("fingerprint", "")) == target:
            matches.append(str(entry["timestamp"]))
    if not matches:
        return PatternHistory(False, (), None, None)
    return PatternHistory(True, tuple(matches), matches[0], matches[-1])


def _save_day_document(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def log_error(
    errors_dir: Path,
    error_type: str,
    message: str,
    *,
    file: str | None = None,
    function: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """
    Append an error entry to today's JSON file unless deduped.

    Deduplication uses error_type + file + function (fingerprint). Duplicate
    fingerprints on the same day are not written twice.

    Returns (entry_or_existing, created) where created is False on dedup hit.
    """

    if not normalize_component(message):
        raise ErrorLearningError("message must be a non-empty string.")
    if not normalize_component(error_type):
        raise ErrorLearningError("error_type must be a non-empty string.")

    today = datetime.now(timezone.utc).date()
    path = day_file_path(errors_dir, today)
    fp = fingerprint(error_type, file, function)

    if path.exists():
        doc = _load_day_file(path)
        if doc is None:
            doc = default_day_document(today)
    else:
        doc = default_day_document(today)

    entries = doc.setdefault("entries", [])
    assert isinstance(entries, list)
    validated: list[dict[str, Any]] = []
    for item in entries:
        try:
            validated.append(validate_entry(item))
        except ErrorLearningError:
            continue

    for existing in validated:
        if existing.get("fingerprint") == fp:
            return existing, False

    new_entry = build_entry(error_type, message, file=file, function=function)
    validated.append(new_entry)
    validated.sort(key=lambda e: str(e["timestamp"]), reverse=True)
    doc["entries"] = validated
    doc["schema_version"] = SCHEMA_VERSION
    doc["date"] = today.isoformat()
    _save_day_document(path, doc)
    return new_entry, True


def suggest_fix_for_entry(entry: dict[str, Any]) -> str:
    """Return a short heuristic suggestion based on error type and message."""

    combined = f"{entry.get('error_type', '')} {entry.get('message', '')}".lower()
    rules: list[tuple[tuple[str, ...], str]] = [
        (("keyerror", "key error"), "Confirm the key exists, or use dict.get with a default before access."),
        (("typeerror", "type error"), "Check argument types, counts, and None values against the callee."),
        (("valueerror", "value error"), "Validate and normalize inputs before passing them to stricter APIs."),
        (("attributeerror",), "Verify the object is the expected type and that the attribute name is correct."),
        (("importerror", "modulenotfound"), "Check PYTHONPATH, venv activation, and dependency installation."),
        (("filenotfound", "no such file"), "Confirm paths exist, cwd, and symlink targets before reading."),
        (("permission", "permission denied"), "Check file ownership, chmod, and whether another process holds a lock."),
        (("timeout",), "Increase timeouts, reduce payload size, or add retries with backoff."),
        (("connection", "econnrefused"), "Verify the service host, port, firewall, and that the daemon is running."),
        (("json", "decode"), "Validate payloads are complete UTF-8 JSON before json.loads."),
        (("memory", "oom"), "Reduce batch size, free caches, or stream results instead of buffering."),
        (("syntaxerror",), "Run the file through the interpreter or linter to locate the bad syntax."),
    ]
    for keywords, hint in rules:
        if any(k in combined for k in keywords):
            return hint
    return "Inspect the stack trace, reproduce minimally, and add logging around the failing boundary."


def format_entry_block(entry: dict[str, Any], *, include_suggestion: bool = False) -> str:
    """Human-readable block for one entry."""

    lines = [
        f"{colorize(str(entry['error_type']), 'red')} {colorize(str(entry['timestamp']), 'cyan')}",
        f"  {colorize('Message:', 'yellow')} {entry['message']}",
    ]
    if entry.get("file"):
        lines.append(f"  {colorize('File:', 'yellow')} {entry['file']}")
    if entry.get("function"):
        lines.append(f"  {colorize('Function:', 'yellow')} {entry['function']}")
    lines.append(f"  {colorize('Fingerprint:', 'yellow')} {entry.get('fingerprint', '')}")
    if include_suggestion:
        lines.append(f"  {colorize('Suggested fix:', 'green')} {suggest_fix_for_entry(entry)}")
    return "\n".join(lines)


def recent_entries(
    errors_dir: Path,
    *,
    limit: int = 20,
    max_days: int | None = 30,
) -> list[dict[str, Any]]:
    """Most recent entries first, optionally limited to files within max_days."""

    all_entries = load_all_entries(errors_dir)
    if max_days is not None and max_days > 0:
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=max_days)
        filtered: list[dict[str, Any]] = []
        for e in all_entries:
            try:
                ts = datetime.fromisoformat(str(e["timestamp"]).replace("Z", "+00:00"))
                if ts.date() >= cutoff:
                    filtered.append(e)
            except ValueError:
                filtered.append(e)
        all_entries = filtered
    all_entries.sort(key=lambda e: str(e["timestamp"]), reverse=True)
    return all_entries[: max(limit, 1)]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenClaw error learning: capture, dedupe, and review failures.")
    parser.add_argument(
        "--errors-dir",
        type=Path,
        default=DEFAULT_ERRORS_DIR,
        help="Directory for per-day JSON logs (default: .learnings/errors under repo root).",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    log_p = sub.add_parser("log", help="Record an error with optional file and function context.")
    log_p.add_argument("error_type", help="Exception class or high-level error label.")
    log_p.add_argument("message", help="Human-readable error message.")
    log_p.add_argument("--file", dest="file", default=None, help="Source file path related to the error.")
    log_p.add_argument("--func", dest="function", default=None, help="Function name related to the error.")

    seen_p = sub.add_parser(
        "seen",
        help="Report whether this error fingerprint was logged before and when.",
    )
    seen_p.add_argument("error_type", help="Same error_type used with log.")
    seen_p.add_argument("--file", dest="file", default=None, help="File path used for fingerprinting.")
    seen_p.add_argument("--func", dest="function", default=None, help="Function name used for fingerprinting.")

    rev_p = sub.add_parser("review", help="Show recent errors with heuristic fix suggestions.")
    rev_p.add_argument("--limit", type=int, default=15, help="Maximum entries to show (default: 15).")
    rev_p.add_argument(
        "--days",
        type=int,
        default=30,
        help="Only include entries from the last N days (0 = all time).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    errors_dir = Path(args.errors_dir).expanduser().resolve()

    if args.command == "log":
        try:
            entry, created = log_error(
                errors_dir,
                args.error_type,
                args.message,
                file=args.file,
                function=args.function,
            )
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1
        if created:
            print(colorize("Logged new error entry.", "green"))
        else:
            print(colorize("Duplicate fingerprint for today; existing entry kept.", "yellow"))
        hist = pattern_seen_before(errors_dir, args.error_type, file=args.file, function=args.function)
        if hist.seen and hist.first_seen_iso != str(entry.get("timestamp")) and hist.first_seen_iso:
            print(
                colorize(
                    f"Previously seen (first: {hist.first_seen_iso}, last: {hist.last_seen_iso}).",
                    "cyan",
                )
            )
        print(format_entry_block(entry, include_suggestion=False))
        return 0

    if args.command == "seen":
        hist = pattern_seen_before(errors_dir, args.error_type, file=args.file, function=args.function)
        if not hist.seen:
            print(colorize("No matching error fingerprint in stored learnings.", "yellow"))
            return 0
        print(colorize("Fingerprint matched stored learnings.", "green"))
        print(f"  {colorize('First seen:', 'yellow')} {hist.first_seen_iso}")
        print(f"  {colorize('Last seen:', 'yellow')} {hist.last_seen_iso}")
        print(f"  {colorize('Occurrences:', 'yellow')} {len(hist.timestamps_iso)}")
        return 0

    if args.command == "review":
        max_days: int | None = args.days if args.days > 0 else None
        try:
            entries = recent_entries(errors_dir, limit=max(args.limit, 1), max_days=max_days)
        except OSError as exc:
            print(colorize(f"Cannot read errors directory: {exc}", "red"), file=sys.stderr)
            return 1
        print(colorize("OpenClaw — recent error learnings", "bold"))
        print(colorize("=" * 40, "cyan"))
        if not entries:
            print(colorize("No entries found.", "yellow"))
            return 0
        for i, entry in enumerate(entries):
            if i:
                print()
            print(format_entry_block(entry, include_suggestion=True))
        return 0

    print(colorize(f"Unknown command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
