#!/usr/bin/env python3
"""Capture and learn from recurring agent session errors with structured context."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


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
FALLBACK_CATEGORY_COLORS = ("red", "yellow", "green")


class ErrorLearningError(RuntimeError):
    """Raised when the error learning log cannot be read or written."""


def colorize(text: str, color: str) -> str:
    """Wrap text in ANSI color codes unless the user disabled them."""

    if os.environ.get("NO_COLOR"):
        return text
    prefix = ANSI.get(color, "")
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI['reset']}"


def normalize_text(text: str) -> str:
    """Normalize free-form text for comparisons and search."""

    return " ".join(text.strip().lower().split())


def category_color(category: str) -> str:
    """Choose a stable display color for a category name."""

    normalized = normalize_text(category)
    if any(token in normalized for token in ("lesson", "resolved", "fix", "success")):
        return "green"
    if any(token in normalized for token in ("warn", "warning", "caution")):
        return "yellow"
    if any(token in normalized for token in ("error", "failure", "fatal", "exception", "crash", "bug")):
        return "red"
    digest = hashlib.sha1(normalized.encode("utf-8")).digest()[0]
    return FALLBACK_CATEGORY_COLORS[digest % len(FALLBACK_CATEGORY_COLORS)]


def _coerce_line(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return n


def _normalize_context_fields(
    category: str,
    *,
    file: str | None,
    line: int | None,
    error_type: str | None,
    user_correction: str | None,
    task_context: str | None,
) -> dict[str, Any]:
    path = (file or "").strip()
    et = (error_type or "").strip() or category.strip() or "unknown"
    uc = (user_correction or "").strip()
    tc = (task_context or "").strip()
    return {
        "file": path or None,
        "line": line,
        "error_type": et,
        "user_correction": uc,
        "task_context": tc or None,
    }


def canonical_payload(
    category: str,
    error: str,
    lesson: str,
    resolved: bool,
    *,
    file: str | None,
    line: int | None,
    error_type: str | None,
    user_correction: str | None,
    task_context: str | None,
) -> dict[str, object]:
    """Return a normalized payload used for IDs and deduplication."""

    ctx = _normalize_context_fields(
        category,
        file=file,
        line=line,
        error_type=error_type,
        user_correction=user_correction,
        task_context=task_context,
    )
    return {
        "category": normalize_text(category),
        "error": normalize_text(error),
        "lesson": normalize_text(lesson),
        "resolved": bool(resolved),
        "file": normalize_text(ctx["file"] or ""),
        "line": ctx["line"],
        "error_type": normalize_text(ctx["error_type"]),
        "user_correction": normalize_text(ctx["user_correction"]),
        "task_context": normalize_text(ctx["task_context"] or ""),
    }


def build_entry(
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
    timestamp: str | None = None,
    file: str | None = None,
    line: int | None = None,
    error_type: str | None = None,
    user_correction: str | None = None,
    task_context: str | None = None,
) -> dict[str, object]:
    """Create a log entry that matches the JSON schema."""

    ctx = _normalize_context_fields(
        category,
        file=file,
        line=line,
        error_type=error_type,
        user_correction=user_correction,
        task_context=task_context,
    )
    payload = canonical_payload(
        category,
        error,
        lesson,
        resolved,
        file=file,
        line=ctx["line"],
        error_type=ctx["error_type"],
        user_correction=ctx["user_correction"],
        task_context=ctx["task_context"],
    )
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    created_at = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    entry: dict[str, object] = {
        "id": digest,
        "timestamp": created_at,
        "category": category.strip(),
        "error": error.strip(),
        "lesson": lesson.strip(),
        "resolved": bool(resolved),
        "file": ctx["file"],
        "line": ctx["line"],
        "error_type": ctx["error_type"],
        "user_correction": ctx["user_correction"],
        "task_context": ctx["task_context"],
    }
    return entry


def default_store() -> dict[str, object]:
    """Return an empty log document."""

    return {"schema_version": SCHEMA_VERSION, "entries": []}


def validate_entry(raw_entry: object) -> dict[str, object]:
    """Validate a single persisted entry and normalize minor omissions."""

    if not isinstance(raw_entry, dict):
        raise ErrorLearningError("Each entry in the error log must be a JSON object.")

    entry = dict(raw_entry)
    required_text_fields = ("timestamp", "category", "error", "lesson")
    for field in required_text_fields:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ErrorLearningError(f"Entry field '{field}' must be a non-empty string.")

    resolved = entry.get("resolved", False)
    if not isinstance(resolved, bool):
        raise ErrorLearningError("Entry field 'resolved' must be a boolean.")

    cat = str(entry["category"])
    file_val = entry.get("file")
    if file_val is not None and not isinstance(file_val, str):
        raise ErrorLearningError("Entry field 'file' must be a string or null.")
    line_val = _coerce_line(entry.get("line"))
    et_raw = entry.get("error_type")
    if et_raw is not None and not isinstance(et_raw, str):
        raise ErrorLearningError("Entry field 'error_type' must be a string.")
    error_type = (str(et_raw).strip() if isinstance(et_raw, str) else "") or cat or "unknown"

    uc_raw = entry.get("user_correction")
    if uc_raw is not None and not isinstance(uc_raw, str):
        raise ErrorLearningError("Entry field 'user_correction' must be a string.")
    user_correction = (str(uc_raw).strip() if isinstance(uc_raw, str) else "")

    tc_raw = entry.get("task_context")
    if tc_raw is not None and not isinstance(tc_raw, str):
        raise ErrorLearningError("Entry field 'task_context' must be a string.")
    task_context = (str(tc_raw).strip() if isinstance(tc_raw, str) else "") or None

    path = (str(file_val).strip() if isinstance(file_val, str) else "") or None

    if not isinstance(entry.get("id"), str) or not str(entry["id"]).strip():
        rebuilt = build_entry(
            cat,
            str(entry["error"]),
            str(entry["lesson"]),
            resolved=resolved,
            timestamp=str(entry["timestamp"]),
            file=path,
            line=line_val,
            error_type=error_type,
            user_correction=user_correction or None,
            task_context=task_context,
        )
        entry["id"] = str(rebuilt["id"])

    entry["resolved"] = resolved
    entry["file"] = path
    entry["line"] = line_val
    entry["error_type"] = error_type
    entry["user_correction"] = user_correction
    entry["task_context"] = task_context
    return {
        "id": str(entry["id"]),
        "timestamp": str(entry["timestamp"]),
        "category": str(entry["category"]),
        "error": str(entry["error"]),
        "lesson": str(entry["lesson"]),
        "resolved": bool(entry["resolved"]),
        "file": entry["file"],
        "line": entry["line"],
        "error_type": str(entry["error_type"]),
        "user_correction": str(entry["user_correction"]),
        "task_context": entry["task_context"],
    }


def load_store(log_path: Path) -> dict[str, object]:
    """Load the persisted error log from disk."""

    if not log_path.exists():
        return default_store()

    try:
        raw = json.loads(log_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErrorLearningError(f"Unable to parse JSON from {log_path}: {exc}") from exc

    if isinstance(raw, list):
        entries = [
            validate_entry(_migrate_legacy_entry(validate_entry_legacy_list_item(item)))
            for item in raw
        ]
        return {"schema_version": SCHEMA_VERSION, "entries": entries}

    if not isinstance(raw, dict):
        raise ErrorLearningError("Error log must contain a JSON object or list of entries.")

    raw_entries = raw.get("entries", [])
    if not isinstance(raw_entries, list):
        raise ErrorLearningError("Error log field 'entries' must be a JSON array.")

    version = int(raw.get("schema_version", 1))
    migrated: list[dict[str, object]] = []
    for item in raw_entries:
        if version < 2:
            migrated.append(validate_entry(_migrate_legacy_entry(validate_entry_legacy_list_item(item))))
        else:
            migrated.append(validate_entry(item))

    return {
        "schema_version": SCHEMA_VERSION,
        "entries": migrated,
    }


def validate_entry_legacy_list_item(raw_entry: object) -> dict[str, object]:
    """Validate legacy v1-shaped entries (no structured context fields)."""

    if not isinstance(raw_entry, dict):
        raise ErrorLearningError("Each entry in the error log must be a JSON object.")
    entry = dict(raw_entry)
    for field in ("timestamp", "category", "error", "lesson"):
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ErrorLearningError(f"Entry field '{field}' must be a non-empty string.")
    resolved = entry.get("resolved", False)
    if not isinstance(resolved, bool):
        raise ErrorLearningError("Entry field 'resolved' must be a boolean.")
    return entry


def _migrate_legacy_entry(entry: dict[str, object]) -> dict[str, object]:
    """Upgrade a v1-style entry to v2 fields for uniform processing."""

    out = dict(entry)
    out.setdefault("file", None)
    out.setdefault("line", None)
    out.setdefault("error_type", str(entry.get("category", "unknown")))
    out.setdefault("user_correction", "")
    out.setdefault("task_context", None)
    return out


def save_store(log_path: Path, store: dict[str, object]) -> None:
    """Persist the error log to disk."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "entries": store.get("entries", []),
    }
    log_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def entries_match(left: dict[str, object], right: dict[str, object]) -> bool:
    """Return True when two entries are the same learning."""

    if left.get("id") == right.get("id"):
        return True
    return canonical_payload(
        str(left["category"]),
        str(left["error"]),
        str(left["lesson"]),
        bool(left["resolved"]),
        file=left.get("file") if left.get("file") else None,
        line=left.get("line") if isinstance(left.get("line"), int) else _coerce_line(left.get("line")),
        error_type=str(left.get("error_type", "")),
        user_correction=str(left.get("user_correction", "")),
        task_context=str(left.get("task_context") or ""),
    ) == canonical_payload(
        str(right["category"]),
        str(right["error"]),
        str(right["lesson"]),
        bool(right["resolved"]),
        file=right.get("file") if right.get("file") else None,
        line=right.get("line") if isinstance(right.get("line"), int) else _coerce_line(right.get("line")),
        error_type=str(right.get("error_type", "")),
        user_correction=str(right.get("user_correction", "")),
        task_context=str(right.get("task_context") or ""),
    )


def add_entry(
    log_path: Path,
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
    file: str | None = None,
    line: int | None = None,
    error_type: str | None = None,
    user_correction: str | None = None,
    task_context: str | None = None,
) -> tuple[dict[str, object], bool]:
    """Add an error learning entry unless it already exists."""

    store = load_store(log_path)
    new_entry = build_entry(
        category,
        error,
        lesson,
        resolved=resolved,
        file=file,
        line=line,
        error_type=error_type,
        user_correction=user_correction,
        task_context=task_context,
    )
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


def log_conversation_error(
    log_path: Path,
    *,
    category: str,
    error: str,
    lesson: str,
    error_type: str | None = None,
    file: str | None = None,
    line: int | None = None,
    user_correction: str | None = None,
    task_context: str | None = None,
    resolved: bool = True,
) -> tuple[dict[str, object], bool]:
    """Log an agent conversation failure with file/line/type and user correction context."""

    return add_entry(
        log_path,
        category,
        error,
        lesson,
        resolved=resolved,
        file=file,
        line=line,
        error_type=error_type,
        user_correction=user_correction,
        task_context=task_context,
    )


def format_entry(entry: dict[str, object]) -> str:
    """Render a single entry for console output."""

    category = str(entry["category"])
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    file_part = entry.get("file")
    line_part = entry.get("line")
    loc = ""
    if file_part:
        loc = f"{file_part}"
        if isinstance(line_part, int):
            loc += f":{line_part}"
    lines = [
        (
            f"{colorize(category, category_color(category))} "
            f"{colorize(f'[{status_text}]', status_color)} "
            f"{colorize(str(entry['timestamp']), 'cyan')}"
        ),
        f"  {colorize('ID:', 'yellow')} {entry['id']}",
        f"  {colorize('Type:', 'cyan')} {entry.get('error_type', '')}",
    ]
    if loc:
        lines.append(f"  {colorize('Location:', 'yellow')} {loc}")
    lines.extend(
        [
            f"  {colorize('Error:', 'red')} {entry['error']}",
            f"  {colorize('Lesson:', 'green')} {entry['lesson']}",
        ]
    )
    uc = str(entry.get("user_correction", "")).strip()
    if uc:
        lines.append(f"  {colorize('User correction:', 'yellow')} {uc}")
    tc = entry.get("task_context")
    if isinstance(tc, str) and tc.strip():
        lines.append(f"  {colorize('Task:', 'cyan')} {tc.strip()}")
    return "\n".join(lines)


def print_entries(entries: list[dict[str, object]], *, heading: str) -> None:
    """Print a collection of entries in a human-readable layout."""

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
    """Print category-level frequency stats."""

    print(colorize("Error Learning Stats", "bold"))
    print(colorize("====================", "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    counts = Counter(str(entry["category"]) for entry in entries)
    total = len(entries)
    for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (count / total) * 100
        print(
            f"- {colorize(category, category_color(category))}: "
            f"{colorize(str(count), 'red')} "
            f"({share:.1f}%)"
        )


def _entry_search_blob(entry: dict[str, object]) -> str:
    parts = [
        str(entry["category"]),
        str(entry["error"]),
        str(entry["lesson"]),
        str(entry.get("error_type", "")),
        str(entry.get("user_correction", "")),
        str(entry.get("file") or ""),
        str(entry.get("task_context") or ""),
    ]
    return " ".join(parts)


def search_score(query: str, entry: dict[str, object]) -> float:
    """Score how relevant an entry is to a search query."""

    normalized_query = normalize_text(query)
    haystack = normalize_text(_entry_search_blob(entry))
    if not normalized_query:
        return 0.0

    substring_bonus = 1.5 if normalized_query in haystack else 0.0
    query_tokens = set(normalized_query.split())
    haystack_tokens = set(haystack.split())
    overlap = len(query_tokens & haystack_tokens) / max(len(query_tokens), 1)
    ratio = SequenceMatcher(None, normalized_query, haystack).ratio()
    task_ctx = normalize_text(str(entry.get("task_context") or ""))
    context_bonus = 0.0
    if task_ctx and query_tokens:
        for token in query_tokens:
            if len(token) >= 3 and token in task_ctx:
                context_bonus += 0.14
    context_bonus = min(context_bonus, 0.42)
    return substring_bonus + overlap + (ratio * 0.5) + context_bonus


def search_entries(
    entries: list[dict[str, object]],
    query: str,
    limit: int = 10,
    *,
    min_score: float = 0.45,
) -> list[dict[str, object]]:
    """Return the most relevant matching entries for the given query."""

    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = search_score(query, entry)
        if score >= min_score:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])), reverse=False)
    return [entry for _, entry in ranked[:limit]]


def retrieve_relevant_errors(
    log_path: Path,
    task_description: str,
    *,
    limit: int = 8,
    min_score: float = 0.35,
) -> list[dict[str, object]]:
    """Load the store and return past errors most relevant to a similar task description."""

    store = load_store(log_path)
    raw_entries = store["entries"]
    assert isinstance(raw_entries, list)
    entries = [validate_entry(e) for e in raw_entries]
    return search_entries(entries, task_description, limit=limit, min_score=min_score)


def format_lessons_block(entries: Iterable[dict[str, object]], *, header: str) -> str:
    """Format retrieved entries as a markdown block for prompt injection."""

    lines = [header.rstrip(), ""]
    for i, entry in enumerate(entries, start=1):
        et = str(entry.get("error_type", entry.get("category", "")))
        loc = ""
        fp = entry.get("file")
        if fp:
            loc = str(fp)
            lp = entry.get("line")
            if isinstance(lp, int):
                loc += f":{lp}"
        lines.append(f"{i}. **{et}**" + (f" (`{loc}`)" if loc else ""))
        lines.append(f"   - Mistake: {entry['error']}")
        lines.append(f"   - Do instead: {entry['lesson']}")
        uc = str(entry.get("user_correction", "")).strip()
        if uc:
            lines.append(f"   - User correction: {uc}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def append_lessons_to_prompt(
    prompt: str,
    task_context: str,
    log_path: Path | None = None,
    *,
    limit: int = 6,
    min_score: float = 0.35,
    header: str = "## Prior mistakes to avoid (error learning log)",
) -> str:
    """Append concise lessons from the log to an agent prompt to reduce repeat failures."""

    path = log_path or DEFAULT_LOG_PATH
    matches = retrieve_relevant_errors(path, task_context, limit=limit, min_score=min_score)
    if not matches:
        return prompt
    block = format_lessons_block(matches, header=header)
    sep = "" if prompt.endswith("\n") else "\n"
    return f"{prompt}{sep}\n{block}"


def aggregate_patterns(
    entries: list[dict[str, object]],
    *,
    group_by: str = "error_type",
    top: int = 25,
) -> list[dict[str, Any]]:
    """
    Group entries for pattern analysis.

    group_by: one of 'error_type', 'file', 'category', or 'file_line' (file + line).
    """

    if group_by not in ("error_type", "file", "category", "file_line"):
        raise ValueError("group_by must be 'error_type', 'file', 'category', or 'file_line'")

    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for entry in entries:
        if group_by == "error_type":
            key = str(entry.get("error_type") or entry.get("category") or "unknown")
        elif group_by == "category":
            key = str(entry["category"])
        elif group_by == "file":
            key = str(entry.get("file") or "(no file)")
        else:
            fp = entry.get("file")
            lp = entry.get("line")
            if fp and isinstance(lp, int):
                key = f"{fp}:{lp}"
            elif fp:
                key = str(fp)
            else:
                key = "(no location)"

        groups[key].append(entry)

    rows: list[dict[str, Any]] = []
    for key, group in groups.items():
        sample = sorted(group, key=lambda e: str(e["timestamp"]), reverse=True)[0]
        rows.append(
            {
                "key": key,
                "count": len(group),
                "open": sum(1 for e in group if not e.get("resolved")),
                "sample_lesson": str(sample.get("lesson", "")),
                "sample_error": str(sample.get("error", "")),
            }
        )
    rows.sort(key=lambda r: (-int(r["count"]), str(r["key"]).lower()))
    return rows[: max(top, 1)]


def print_patterns(entries: list[dict[str, object]], *, group_by: str, top: int) -> None:
    """Print aggregated error patterns for CLI inspection."""

    patterns = aggregate_patterns(entries, group_by=group_by, top=top)
    title = f"Error patterns (by {group_by})"
    print(colorize(title, "bold"))
    print(colorize("=" * len(title), "cyan"))
    if not patterns:
        print(colorize("No entries to aggregate.", "yellow"))
        return
    for row in patterns:
        key = str(row["key"])
        count = int(row["count"])
        open_n = int(row["open"])
        print(
            f"- {colorize(key, category_color(key))}: "
            f"{colorize(str(count), 'red')} total"
            + (f", {colorize(str(open_n), 'yellow')} open" if open_n else "")
        )
        print(f"    sample: {row['sample_error'][:120]}{'…' if len(row['sample_error']) > 120 else ''}")
        print(f"    lesson: {row['sample_lesson'][:120]}{'…' if len(row['sample_lesson']) > 120 else ''}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Capture agent conversation errors, retrieve relevant learnings, and aggregate patterns."
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to the error learning JSON log.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new error learning entry.")
    add_parser.add_argument("error_category", help="High-level category for the error.")
    add_parser.add_argument("error_message", help="Error message or failure summary.")
    add_parser.add_argument("lesson_learned", help="Lesson learned from the failure.")
    add_parser.add_argument("--file", dest="file", default=None, help="Related file path.")
    add_parser.add_argument("--line", type=int, default=None, help="Line number in the file.")
    add_parser.add_argument(
        "--error-type",
        dest="error_type",
        default=None,
        help="Machine-friendly error type (defaults to category).",
    )
    add_parser.add_argument("--user-correction", default=None, help="What the user said or did to correct the agent.")
    add_parser.add_argument("--task-context", default=None, help="Short description of the task being run.")
    resolved_group = add_parser.add_mutually_exclusive_group()
    resolved_group.add_argument(
        "--resolved",
        dest="resolved",
        action="store_true",
        default=True,
        help="Mark the entry as resolved (default).",
    )
    resolved_group.add_argument(
        "--unresolved",
        dest="resolved",
        action="store_false",
        help="Mark the entry as still open.",
    )

    subparsers.add_parser("list", help="List all learned errors.")
    subparsers.add_parser("stats", help="Show error frequency by category.")

    search_parser = subparsers.add_parser("search", help="Search for relevant past errors.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of matching entries to print.",
    )

    patterns_parser = subparsers.add_parser("patterns", help="Aggregate and view recurring error patterns.")
    patterns_parser.add_argument(
        "--by",
        dest="group_by",
        choices=("error_type", "file", "category", "file_line"),
        default="error_type",
        help="Grouping key for aggregation.",
    )
    patterns_parser.add_argument("--top", type=int, default=25, help="Maximum number of pattern rows to show.")
    patterns_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit aggregation as JSON instead of text.",
    )

    prompt_parser = subparsers.add_parser(
        "prompt-append",
        help="Print base prompt with relevant lessons appended (for agent wrappers).",
    )
    prompt_parser.add_argument(
        "base_prompt",
        nargs="?",
        default="",
        help="Base prompt text; if omitted, read stdin.",
    )
    prompt_parser.add_argument(
        "--task-context",
        required=True,
        help="Task description used to match relevant past errors.",
    )
    prompt_parser.add_argument("--limit", type=int, default=6, help="Maximum lessons to append.")
    prompt_parser.add_argument(
        "--min-score",
        type=float,
        default=0.35,
        help="Minimum relevance score for inclusion (see search_entries).",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the error learning CLI."""

    args = parse_args(argv)
    try:
        store = load_store(args.log_path)
    except ErrorLearningError as exc:
        print(colorize(str(exc), "red"), file=sys.stderr)
        return 1

    entries = store["entries"]
    assert isinstance(entries, list)

    if args.command == "add":
        try:
            entry, created = add_entry(
                args.log_path,
                args.error_category,
                args.error_message,
                args.lesson_learned,
                resolved=args.resolved,
                file=args.file,
                line=args.line,
                error_type=args.error_type,
                user_correction=args.user_correction,
                task_context=args.task_context,
            )
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1

        if created:
            print(colorize("Saved error learning entry.", "green"))
        else:
            print(colorize("Duplicate entry detected; existing learning kept.", "yellow"))
        print(format_entry(entry))
        return 0

    validated_entries = [validate_entry(entry) for entry in entries]

    if args.command == "list":
        print_entries(validated_entries, heading="Agent Error Learnings")
        return 0

    if args.command == "stats":
        print_stats(validated_entries)
        return 0

    if args.command == "search":
        matches = search_entries(validated_entries, args.query, limit=max(args.limit, 1))
        print_entries(matches, heading=f"Search Results: {args.query}")
        return 0

    if args.command == "patterns":
        try:
            patterns = aggregate_patterns(
                validated_entries,
                group_by=args.group_by,
                top=max(args.top, 1),
            )
        except ValueError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(patterns, indent=2))
        else:
            print_patterns(validated_entries, group_by=args.group_by, top=max(args.top, 1))
        return 0

    if args.command == "prompt-append":
        base = args.base_prompt
        if not base and not sys.stdin.isatty():
            base = sys.stdin.read()
        elif not base:
            print(colorize("Provide base_prompt or pipe prompt on stdin.", "red"), file=sys.stderr)
            return 1
        out = append_lessons_to_prompt(
            base,
            args.task_context,
            args.log_path,
            limit=max(args.limit, 1),
            min_score=float(args.min_score),
        )
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
