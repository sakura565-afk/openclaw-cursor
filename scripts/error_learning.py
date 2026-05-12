#!/usr/bin/env python3
"""Track agent errors for OpenClaw: JSON-backed log with structured fields.

Each entry stores: timestamp, error_type, message, context (JSON value),
and suggested_fix. Use as a module or via the CLI.

Demo (no confirmation prompts):

    python3 scripts/error_learning.py demo

Programmatic example:

    from pathlib import Path
    from scripts.error_learning import AgentErrorJournal

    journal = AgentErrorJournal(Path(".learnings/agent_errors.json"))
    journal.record(
        error_type="tool_timeout",
        message="mcp server did not respond within 30s",
        context={"tool": "read_file", "session": "abc-123"},
        suggested_fix="Increase MCP client timeout or retry with smaller batches.",
    )
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = ROOT / ".learnings" / "error_log.json"
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
    if os.environ.get("NO_COLOR"):
        return text
    prefix = ANSI.get(color, "")
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI['reset']}"


def normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def type_color(error_type: str) -> str:
    normalized = normalize_text(error_type)
    if any(token in normalized for token in ("lesson", "resolved", "fix", "success")):
        return "green"
    if any(token in normalized for token in ("warn", "warning", "caution")):
        return "yellow"
    if any(token in normalized for token in ("error", "failure", "fatal", "exception", "crash", "bug")):
        return "red"
    digest = hashlib.sha1(normalized.encode("utf-8")).digest()[0]
    return FALLBACK_TYPE_COLORS[digest % len(FALLBACK_TYPE_COLORS)]


def canonical_json(value: Any) -> str:
    """Stable JSON string for hashing and deduplication."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_payload(
    error_type: str,
    message: str,
    context: Any,
    suggested_fix: str,
) -> dict[str, object]:
    return {
        "error_type": normalize_text(error_type),
        "message": normalize_text(message),
        "context": canonical_json(context),
        "suggested_fix": normalize_text(suggested_fix),
    }


def utc_timestamp_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_entry(
    error_type: str,
    message: str,
    context: Any,
    suggested_fix: str,
    *,
    timestamp: str | None = None,
) -> dict[str, object]:
    payload = canonical_payload(error_type, message, context, suggested_fix)
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    return {
        "id": digest,
        "timestamp": timestamp or utc_timestamp_iso(),
        "error_type": error_type.strip(),
        "message": message.strip(),
        "context": context if isinstance(context, (dict, list)) else context,
        "suggested_fix": suggested_fix.strip(),
    }


def default_store() -> dict[str, object]:
    return {"schema_version": SCHEMA_VERSION, "entries": []}


def _migrate_legacy_entry(raw: Mapping[str, Any]) -> dict[str, object]:
    """Map v1 entries (category, error, lesson, resolved) to v2 shape."""

    resolved = bool(raw.get("resolved", False))
    legacy_ctx: dict[str, Any] = {}
    if "resolved" in raw:
        legacy_ctx["legacy_resolved"] = resolved
    category = str(raw.get("category", "")).strip() or "unknown"
    message = str(raw.get("error", "")).strip() or "(migrated) empty error"
    lesson = str(raw.get("lesson", "")).strip() or "(migrated) empty lesson"
    ts = str(raw.get("timestamp", "")).strip() or utc_timestamp_iso()
    return build_entry(
        category,
        message,
        legacy_ctx if legacy_ctx else {},
        lesson,
        timestamp=ts,
    )


def validate_entry(raw_entry: object) -> dict[str, object]:
    if not isinstance(raw_entry, dict):
        raise ErrorLearningError("Each entry in the error log must be a JSON object.")

    entry_any: dict[str, Any] = dict(raw_entry)

    if "error_type" not in entry_any and "category" in entry_any:
        return _migrate_legacy_entry(entry_any)

    for field in ("timestamp", "error_type", "message", "suggested_fix"):
        value = entry_any.get(field)
        if not isinstance(value, str) or not str(value).strip():
            raise ErrorLearningError(f"Entry field '{field}' must be a non-empty string.")

    ctx = entry_any.get("context")
    if ctx is None:
        ctx = {}
    try:
        json.dumps(ctx, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ErrorLearningError("Entry field 'context' must be JSON-serializable.") from exc

    entry: dict[str, object] = {
        "timestamp": str(entry_any["timestamp"]).strip(),
        "error_type": str(entry_any["error_type"]).strip(),
        "message": str(entry_any["message"]).strip(),
        "context": ctx,
        "suggested_fix": str(entry_any["suggested_fix"]).strip(),
    }

    eid = entry_any.get("id")
    if not isinstance(eid, str) or not eid.strip():
        merged = build_entry(
            str(entry["error_type"]),
            str(entry["message"]),
            entry["context"],
            str(entry["suggested_fix"]),
            timestamp=str(entry["timestamp"]),
        )
        entry["id"] = str(merged["id"])
    else:
        entry["id"] = eid.strip()

    return entry


def load_store(log_path: Path) -> dict[str, object]:
    if not log_path.exists():
        return default_store()

    raw_text = log_path.read_text(encoding="utf-8").strip()
    if not raw_text:
        return default_store()

    try:
        raw = json.loads(raw_text)
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
    return {
        "schema_version": max(version, SCHEMA_VERSION),
        "entries": [validate_entry(item) for item in raw_entries],
    }


def save_store(log_path: Path, store: dict[str, object]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    to_write = dict(store)
    to_write["schema_version"] = SCHEMA_VERSION
    log_path.write_text(json.dumps(to_write, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def entries_match(left: dict[str, object], right: dict[str, object]) -> bool:
    if left.get("id") == right.get("id"):
        return True
    return canonical_payload(
        str(left["error_type"]),
        str(left["message"]),
        left["context"],
        str(left["suggested_fix"]),
    ) == canonical_payload(
        str(right["error_type"]),
        str(right["message"]),
        right["context"],
        str(right["suggested_fix"]),
    )


def add_entry(
    log_path: Path,
    error_type: str,
    message: str,
    suggested_fix: str,
    *,
    context: Any | None = None,
) -> tuple[dict[str, object], bool]:
    store = load_store(log_path)
    ctx: Any = {} if context is None else context
    new_entry = build_entry(error_type, message, ctx, suggested_fix)
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


@dataclass
class AgentErrorJournal:
    """JSON-backed journal of agent errors."""

    path: Path

    def record(
        self,
        *,
        error_type: str,
        message: str,
        suggested_fix: str,
        context: Any | None = None,
    ) -> tuple[dict[str, object], bool]:
        return add_entry(self.path, error_type, message, suggested_fix, context=context)

    def load(self) -> dict[str, object]:
        return load_store(self.path)

    def entries(self) -> list[dict[str, object]]:
        store = self.load()
        raw = store["entries"]
        assert isinstance(raw, list)
        return [validate_entry(e) for e in raw]


def format_entry(entry: dict[str, object]) -> str:
    error_type = str(entry["error_type"])
    ctx_display = canonical_json(entry["context"])
    if len(ctx_display) > 160:
        ctx_display = ctx_display[:157] + "..."
    lines = [
        (
            f"{colorize(error_type, type_color(error_type))} "
            f"{colorize(str(entry['timestamp']), 'cyan')}"
        ),
        f"  {colorize('ID:', 'yellow')} {entry['id']}",
        f"  {colorize('Message:', 'red')} {entry['message']}",
        f"  {colorize('Context:', 'yellow')} {ctx_display}",
        f"  {colorize('Suggested fix:', 'green')} {entry['suggested_fix']}",
    ]
    return "\n".join(lines)


def print_entries(entries: list[dict[str, object]], *, heading: str) -> None:
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
    print(colorize("Agent error stats", "bold"))
    print(colorize("=================", "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    counts = Counter(str(entry["error_type"]) for entry in entries)
    total = len(entries)
    for error_type, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (count / total) * 100
        print(
            f"- {colorize(error_type, type_color(error_type))}: "
            f"{colorize(str(count), 'red')} "
            f"({share:.1f}%)"
        )


def search_score(query: str, entry: dict[str, object]) -> float:
    normalized_query = normalize_text(query)
    haystack = normalize_text(
        " ".join(
            (
                str(entry["error_type"]),
                str(entry["message"]),
                str(entry["suggested_fix"]),
                canonical_json(entry["context"]),
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


def search_entries(entries: list[dict[str, object]], query: str, limit: int = 10) -> list[dict[str, object]]:
    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = search_score(query, entry)
        if score >= 0.45:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])), reverse=False)
    return [entry for _, entry in ranked[:limit]]


def parse_context_arg(raw: str | None) -> Any:
    if raw is None or raw == "":
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ErrorLearningError(f"Invalid JSON for --context: {exc}") from exc


def run_demo() -> int:
    """Print and perform a small end-to-end demo (uses a temporary file)."""

    import tempfile

    print(colorize("Agent error learning — demo", "bold"))
    print()

    demo_path = Path(tempfile.mkdtemp()) / "agent_errors_demo.json"

    try:
        journal = AgentErrorJournal(demo_path)
        rec, created = journal.record(
            error_type="validation_error",
            message="Model output failed schema check for tool arguments.",
            context={"tool": "run_terminal_cmd", "field": "command"},
            suggested_fix="Constrain the prompt with the exact JSON schema and add one-shot examples.",
        )
        print(colorize("Recorded (created=%s):" % created, "green"))
        print(format_entry(rec))
        print()

        dup, created2 = journal.record(
            error_type="validation_error",
            message="Model output failed schema check for tool arguments.",
            context={"tool": "run_terminal_cmd", "field": "command"},
            suggested_fix="Constrain the prompt with the exact JSON schema and add one-shot examples.",
        )
        print(colorize("Duplicate record (created=%s) — same id:" % created2, "yellow"))
        print(f"  id={dup['id']}")
        print()

        journal.record(
            error_type="rate_limit",
            message="HTTP 429 from upstream API after burst traffic.",
            context={"provider": "example", "retry_after_s": 60},
            suggested_fix="Exponential backoff and reduce parallel requests.",
        )

        print(colorize("All entries on disk:", "bold"))
        print_entries(journal.entries(), heading="Demo journal")
        print()
        print(colorize("Search 'schema':", "bold"))
        print_entries(search_entries(journal.entries(), "schema"), heading="Matches")
    finally:
        demo_path.unlink(missing_ok=True)
        try:
            demo_path.parent.rmdir()
        except OSError:
            pass

    print()
    print(colorize("Demo complete (temporary file removed).", "cyan"))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track agent errors with JSON storage.")
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to the JSON error log.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Append an error record.")
    add_parser.add_argument("error_type", help="Short error classification (e.g. timeout, parse_error).")
    add_parser.add_argument("message", help="Human-readable error message.")
    add_parser.add_argument("suggested_fix", help="Suggested remediation.")
    add_parser.add_argument(
        "--context",
        default=None,
        help='JSON object for extra context (default: {}). Example: \'{"tool":"read_file"}\'',
    )

    subparsers.add_parser("list", help="List all entries.")
    subparsers.add_parser("stats", help="Show counts by error_type.")
    subparsers.add_parser("demo", help="Run an in-memory/temp-file demo and exit.")

    search_parser = subparsers.add_parser("search", help="Search message, type, fix, and context.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument("--limit", type=int, default=10, help="Max results.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.command == "demo":
        return run_demo()

    try:
        store = load_store(args.log_path)
    except ErrorLearningError as exc:
        print(colorize(str(exc), "red"), file=sys.stderr)
        return 1

    entries = store["entries"]
    assert isinstance(entries, list)

    if args.command == "add":
        try:
            ctx = parse_context_arg(args.context)
            entry, created = add_entry(
                args.log_path,
                args.error_type,
                args.message,
                args.suggested_fix,
                context=ctx,
            )
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1

        if created:
            print(colorize("Saved agent error entry.", "green"))
        else:
            print(colorize("Duplicate entry detected; existing record kept.", "yellow"))
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


if __name__ == "__main__":
    raise SystemExit(main())
