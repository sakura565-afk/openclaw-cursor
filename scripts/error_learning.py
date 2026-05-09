#!/usr/bin/env python3
"""Track agent errors for learning: JSON-backed log with timestamp, type, message, context, and fix."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = ROOT / ".learnings" / "agent_errors.json"
SCHEMA_VERSION = 2
ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}
FALLBACK_TYPE_COLORS = ("red", "yellow", "green")


class ErrorLearningError(RuntimeError):
    """Raised when the error log cannot be read or written."""


def colorize(text: str, color: str) -> str:
    """Wrap text in ANSI color codes unless NO_COLOR is set."""

    if os.environ.get("NO_COLOR"):
        return text
    prefix = ANSI.get(color, "")
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI['reset']}"


def normalize_text(text: str) -> str:
    """Normalize free-form text for comparisons and search."""

    return " ".join(text.strip().lower().split())


def error_type_color(error_type: str) -> str:
    """Choose a stable display color for an error type label."""

    normalized = normalize_text(error_type)
    if any(token in normalized for token in ("resolved", "fix", "success", "recovered")):
        return "green"
    if any(token in normalized for token in ("warn", "warning", "caution", "timeout")):
        return "yellow"
    if any(
        token in normalized
        for token in ("error", "failure", "fatal", "exception", "crash", "bug")
    ):
        return "red"
    digest = hashlib.sha1(normalized.encode("utf-8")).digest()[0]
    return FALLBACK_TYPE_COLORS[digest % len(FALLBACK_TYPE_COLORS)]


def canonical_payload(
    error_type: str,
    message: str,
    context: str,
    suggested_fix: str,
) -> dict[str, object]:
    """Normalized payload for IDs and deduplication."""

    return {
        "error_type": normalize_text(error_type),
        "message": normalize_text(message),
        "context": normalize_text(context),
        "suggested_fix": normalize_text(suggested_fix),
    }


def build_entry(
    error_type: str,
    message: str,
    context: str,
    suggested_fix: str,
    *,
    timestamp: str | None = None,
) -> dict[str, object]:
    """Create one log entry matching the JSON schema."""

    payload = canonical_payload(error_type, message, context, suggested_fix)
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    created_at = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    return {
        "id": digest,
        "timestamp": created_at,
        "error_type": error_type.strip(),
        "message": message.strip(),
        "context": context.strip(),
        "suggested_fix": suggested_fix.strip(),
    }


def default_store() -> dict[str, object]:
    """Return an empty log document."""

    return {"schema_version": SCHEMA_VERSION, "entries": []}


def _migrate_legacy_entry(raw: dict[str, object]) -> dict[str, object]:
    """Map v1 entries (category/error/lesson) to the current schema."""

    if "error_type" in raw and "message" in raw:
        return raw
    if "category" in raw and "error" in raw and "lesson" in raw:
        ctx = raw.get("context")
        if not isinstance(ctx, str):
            ctx = ""
        return {
            "id": raw.get("id"),
            "timestamp": raw["timestamp"],
            "error_type": raw["category"],
            "message": raw["error"],
            "context": ctx.strip(),
            "suggested_fix": raw["lesson"],
        }
    return raw


def validate_entry(raw_entry: object) -> dict[str, object]:
    """Validate and normalize one persisted entry."""

    if not isinstance(raw_entry, dict):
        raise ErrorLearningError("Each entry in the error log must be a JSON object.")

    entry = dict(_migrate_legacy_entry(raw_entry))
    required_text = ("timestamp", "error_type", "message", "context", "suggested_fix")
    for field in required_text:
        value = entry.get(field)
        if not isinstance(value, str):
            raise ErrorLearningError(f"Entry field '{field}' must be a string.")
        if field != "context" and not value.strip():
            raise ErrorLearningError(f"Entry field '{field}' must be non-empty (context may be empty).")

    if not isinstance(entry.get("id"), str) or not str(entry["id"]).strip():
        entry["id"] = build_entry(
            entry["error_type"],
            entry["message"],
            entry["context"],
            entry["suggested_fix"],
            timestamp=entry["timestamp"],
        )["id"]
    return entry


def load_store(log_path: Path) -> dict[str, object]:
    """Load the persisted error log from disk."""

    if not log_path.exists():
        return default_store()

    try:
        raw = json.loads(log_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErrorLearningError(f"Unable to parse JSON from {log_path}: {exc}") from exc

    if isinstance(raw, list):
        entries = [validate_entry(item) for item in raw]
        return {"schema_version": SCHEMA_VERSION, "entries": entries}

    if not isinstance(raw, dict):
        raise ErrorLearningError("Error log must contain a JSON object or list of entries.")

    raw_entries = raw.get("entries", [])
    if not isinstance(raw_entries, list):
        raise ErrorLearningError("Error log field 'entries' must be a JSON array.")

    version = int(raw.get("schema_version", SCHEMA_VERSION))
    store = {
        "schema_version": max(version, SCHEMA_VERSION),
        "entries": [validate_entry(item) for item in raw_entries],
    }
    return store


def save_store(log_path: Path, store: dict[str, object]) -> None:
    """Persist the error log to disk."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    out = dict(store)
    out["schema_version"] = SCHEMA_VERSION
    log_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")


def entries_match(left: dict[str, object], right: dict[str, object]) -> bool:
    """True when two entries represent the same learning (by id or canonical payload)."""

    if left.get("id") == right.get("id"):
        return True
    return canonical_payload(
        str(left["error_type"]),
        str(left["message"]),
        str(left["context"]),
        str(left["suggested_fix"]),
    ) == canonical_payload(
        str(right["error_type"]),
        str(right["message"]),
        str(right["context"]),
        str(right["suggested_fix"]),
    )


def add_entry(
    log_path: Path,
    error_type: str,
    message: str,
    context: str,
    suggested_fix: str,
) -> tuple[dict[str, object], bool]:
    """Append an error entry unless an equivalent one already exists."""

    store = load_store(log_path)
    new_entry = build_entry(error_type, message, context, suggested_fix)
    entries = store["entries"]
    assert isinstance(entries, list)
    for entry in entries:
        validated = validate_entry(entry)
        if entries_match(validated, new_entry):
            return validated, False

    entries.append(new_entry)
    entries.sort(key=lambda item: str(item["timestamp"]), reverse=True)
    save_store(log_path, store)
    return new_entry, True


def format_entry(entry: dict[str, object]) -> str:
    """Render one entry for the console."""

    et = str(entry["error_type"])
    lines = [
        f"{colorize(et, error_type_color(et))} {colorize(str(entry['timestamp']), 'cyan')}",
        f"  {colorize('ID:', 'yellow')} {entry['id']}",
        f"  {colorize('Message:', 'red')} {entry['message']}",
        f"  {colorize('Context:', 'yellow')} {entry['context'] or '(none)'}",
        f"  {colorize('Suggested fix:', 'green')} {entry['suggested_fix']}",
    ]
    return "\n".join(lines)


def print_entries(entries: list[dict[str, object]], *, heading: str) -> None:
    """Print entries in a human-readable layout."""

    print(colorize(heading, "bold"))
    print(colorize("=" * len(heading), "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    for index, entry in enumerate(entries):
        if index:
            print()
        print(format_entry(entry))


def print_stats(entries: list[dict[str, object]]) -> None:
    """Print frequency by error type."""

    print(colorize("Agent error stats (by type)", "bold"))
    print(colorize("===========================", "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    counts = Counter(str(entry["error_type"]) for entry in entries)
    total = len(entries)
    for error_type, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (count / total) * 100
        print(
            f"- {colorize(error_type, error_type_color(error_type))}: "
            f"{colorize(str(count), 'red')} ({share:.1f}%)"
        )


def search_score(query: str, entry: dict[str, object]) -> float:
    """Relevance score for a free-text query."""

    normalized_query = normalize_text(query)
    haystack = normalize_text(
        " ".join(
            (
                str(entry["error_type"]),
                str(entry["message"]),
                str(entry["context"]),
                str(entry["suggested_fix"]),
            )
        )
    )
    if not normalized_query:
        return 0.0

    substring_bonus = 1.5 if normalized_query in haystack else 0.0
    query_tokens = set(normalized_query.split())
    haystack_tokens = set(haystack.split())
    overlap = len(query_tokens & haystack_tokens) / max(len(query_tokens), 1)
    ratio = SequenceMatcher(None, normalized_query, haystack).ratio()
    return substring_bonus + overlap + (ratio * 0.5)


def search_entries(
    entries: list[dict[str, object]], query: str, limit: int = 10
) -> list[dict[str, object]]:
    """Return the best matching entries for query."""

    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = search_score(query, entry)
        if score >= 0.45:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])), reverse=False)
    return [entry for _, entry in ranked[:limit]]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Track agent errors (JSON) with type, message, context, and suggested fix."
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to the JSON error log.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Record a new agent error.")
    add_parser.add_argument("error_type", help="Error classification (e.g. tool_timeout, parse_error).")
    add_parser.add_argument("message", help="Error message or short summary.")
    add_parser.add_argument(
        "context",
        nargs="?",
        default="",
        help="Where it happened (session id, file, tool name). Omit for empty context.",
    )
    add_parser.add_argument(
        "suggested_fix",
        nargs="?",
        default="",
        help="What to try next time. If omitted, provide via --fix.",
    )
    add_parser.add_argument(
        "--fix",
        dest="fix_flag",
        default=None,
        help="Suggested fix when the last positional arg would be ambiguous.",
    )

    subparsers.add_parser("list", help="List all recorded errors.")
    subparsers.add_parser("stats", help="Show counts by error type.")

    search_parser = subparsers.add_parser("search", help="Search message, context, type, and fixes.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of matches to print.",
    )
    return parser.parse_args(argv)


def _normalize_add_positional(args: argparse.Namespace) -> tuple[str, str, str, str]:
    """Resolve add-command positional ambiguity (optional context/fix)."""

    et = args.error_type
    msg = args.message
    ctx = args.context or ""
    fix = args.suggested_fix or ""
    if args.fix_flag is not None:
        fix = args.fix_flag
    # If only two positionals: error_type, message — context and fix empty is valid only if user passed --fix
    # If three: error_type, message, context — fix may be empty
    # If four: all set
    return et, msg, ctx, fix


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""

    args = parse_args(argv)
    try:
        store = load_store(args.log_path)
    except ErrorLearningError as exc:
        print(colorize(str(exc), "red"), file=sys.stderr)
        return 1

    entries = store["entries"]
    assert isinstance(entries, list)

    if args.command == "add":
        et, msg, ctx, fix = _normalize_add_positional(args)
        if not fix.strip():
            print(
                colorize(
                    "suggested_fix is required: pass a fourth argument or use --fix \"...\".",
                    "red",
                ),
                file=sys.stderr,
            )
            return 1
        try:
            entry, created = add_entry(args.log_path, et, msg, ctx, fix)
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1

        if created:
            print(colorize("Saved agent error entry.", "green"))
        else:
            print(colorize("Duplicate entry detected; keeping existing record.", "yellow"))
        print(format_entry(entry))
        return 0

    validated_entries = [validate_entry(entry) for entry in entries]

    if args.command == "list":
        print_entries(validated_entries, heading="Agent error log")
        return 0

    if args.command == "stats":
        print_stats(validated_entries)
        return 0

    if args.command == "search":
        matches = search_entries(validated_entries, args.query, limit=max(args.limit, 1))
        print_entries(matches, heading=f"Search results: {args.query}")
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


def demo() -> None:
    """
    Demo usage: in-memory examples and a short-lived JSON file.

    Run: python3 scripts/error_learning.py
    Or:  python3 scripts/error_learning.py --demo
    """
    print(colorize("error_learning.py — demo", "bold"))
    sample = build_entry(
        "tool_timeout",
        "Shell command exceeded 120s budget",
        "session=abc123, tool=run_terminal_cmd",
        "Increase block_until_ms or split the command into smaller steps.",
    )
    print("\n" + colorize("1) Example entry (dict):", "cyan"))
    print(json.dumps(sample, indent=2))

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "demo_agent_errors.json"
        add_entry(
            path,
            "parse_error",
            "Model returned markdown instead of JSON",
            "task=context_optimization",
            "Add an explicit 'return only JSON' line and validate with json.loads.",
        )
        add_entry(
            path,
            "permission_denied",
            "Write refused under /etc",
            "script=deploy.sh",
            "Use a user-writable staging path or request elevated scope only when needed.",
        )
        store = load_store(path)
        print("\n" + colorize("2) Persisted store (two entries):", "cyan"))
        print(json.dumps(store, indent=2))

    print("\n" + colorize("3) CLI examples:", "cyan"))
    print("  python3 scripts/error_learning.py add tool_failure 'API 503' 'tool=weather' --fix 'Retry with backoff'")
    print("  python3 scripts/error_learning.py list --log-path .learnings/agent_errors.json")
    print("  python3 scripts/error_learning.py search 'json' --limit 5")


if __name__ == "__main__":
    if len(sys.argv) == 1 or sys.argv[1] in ("--demo", "-demo"):
        demo()
        raise SystemExit(0)
    raise SystemExit(main())
