#!/usr/bin/env python3
"""Capture and learn from recurring OpenClaw session errors."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = ROOT / ".learnings" / "error_log.json"
DEFAULT_WORKSPACE_ROOT = ROOT
DEFAULT_NEAR_DUPLICATE_THRESHOLD = 0.88
MAX_MEMORY_FILE_BYTES = 512 * 1024
SCHEMA_VERSION = 1
MARKDOWN_AUTO_BEGIN = "<!-- BEGIN_AUTO_ERROR_LOG -->"
MARKDOWN_AUTO_END = "<!-- END_AUTO_ERROR_LOG -->"

ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}
FALLBACK_CATEGORY_COLORS = ("red", "yellow", "green")

# (needle substring after normalize_text, category slug) — order is specificity-first.
ERROR_TYPE_KEYWORD_RULES: tuple[tuple[str, str], ...] = (
    ("merge conflict", "git"),
    ("git push", "git"),
    ("git pull", "git"),
    ("git clone", "git"),
    ("github api", "git"),
    ("subprocess", "subprocess"),
    ("exit code", "subprocess"),
    ("non-zero exit", "subprocess"),
    ("permission denied", "permission"),
    ("eacces", "permission"),
    ("eperm", "permission"),
    ("modulenotfounderror", "import"),
    ("importerror", "import"),
    ("no module named", "import"),
    ("json.decoder", "json_parse"),
    ("jsondecodeerror", "json_parse"),
    ("invalid json", "json_parse"),
    ("unexpected token", "json_parse"),
    ("yaml", "yaml_parse"),
    ("yamerror", "yaml_parse"),
    ("connection refused", "network"),
    ("econnrefused", "network"),
    ("name or service not known", "network"),
    ("temporary failure", "network"),
    ("ssl error", "network"),
    ("certificate", "network"),
    ("rate limit", "rate_limit"),
    ("too many requests", "rate_limit"),
    (" 429 ", "rate_limit"),
    ("timed out", "timeout"),
    ("timeout", "timeout"),
    ("deadline exceeded", "timeout"),
    ("ollama", "ollama"),
    ("cuda error", "gpu"),
    ("out of memory", "resource"),
    ("enospc", "resource"),
    ("disk space", "resource"),
    ("enoent", "filesystem"),
    ("file not found", "filesystem"),
    ("not a directory", "filesystem"),
    ("traceback", "runtime_exception"),
    ("exception", "runtime_exception"),
    ("assertionerror", "test_failure"),
    ("validation error", "validation"),
    ("422", "validation"),
    ("401", "auth"),
    ("403", "auth"),
    ("unauthorized", "auth"),
    ("parser", "parsing"),
    ("parse error", "parsing"),
    ("structured output", "parsing"),
)


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


def signature_text(error: str, lesson: str) -> str:
    """Combined normalized text for fuzzy deduplication."""

    return normalize_text(f"{error} {lesson}")


def infer_error_category(error: str, lesson: str) -> str:
    """Pick a category slug from error-type keywords in error and lesson."""

    haystack = normalize_text(f"{error} {lesson}")
    for needle, category in ERROR_TYPE_KEYWORD_RULES:
        if needle in haystack:
            return category
    return "general"


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


def canonical_payload(category: str, error: str, lesson: str, resolved: bool) -> dict[str, object]:
    """Return a normalized payload used for IDs and deduplication."""

    return {
        "category": normalize_text(category),
        "error": normalize_text(error),
        "lesson": normalize_text(lesson),
        "resolved": bool(resolved),
    }


def build_entry(
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
    timestamp: str | None = None,
) -> dict[str, object]:
    """Create a log entry that matches the JSON schema."""

    payload = canonical_payload(category, error, lesson, resolved)
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    created_at = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "id": digest,
        "timestamp": created_at,
        "category": category.strip(),
        "error": error.strip(),
        "lesson": lesson.strip(),
        "resolved": bool(resolved),
    }


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

    if not isinstance(entry.get("id"), str) or not entry["id"].strip():
        entry["id"] = build_entry(
            entry["category"],
            entry["error"],
            entry["lesson"],
            resolved=resolved,
            timestamp=entry["timestamp"],
        )["id"]
    entry["resolved"] = resolved
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

    return {
        "schema_version": int(raw.get("schema_version", SCHEMA_VERSION)),
        "entries": [validate_entry(item) for item in raw_entries],
    }


def save_store(log_path: Path, store: dict[str, object]) -> None:
    """Persist the error log to disk."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")


def entries_match(left: dict[str, object], right: dict[str, object]) -> bool:
    """Return True when two entries are the same learning."""

    if left.get("id") == right.get("id"):
        return True
    return canonical_payload(
        str(left["category"]),
        str(left["error"]),
        str(left["lesson"]),
        bool(left["resolved"]),
    ) == canonical_payload(
        str(right["category"]),
        str(right["error"]),
        str(right["lesson"]),
        bool(right["resolved"]),
    )


def fuzzy_similarity(a: str, b: str) -> float:
    """Ratio in [0, 1] for near-duplicate detection."""

    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def find_near_duplicate(
    new_entry: dict[str, object],
    entries: list[dict[str, object]],
    *,
    threshold: float,
) -> dict[str, object] | None:
    """Return an existing entry that is a near-duplicate of new_entry, if any."""

    new_sig = signature_text(str(new_entry["error"]), str(new_entry["lesson"]))
    best: tuple[float, dict[str, object] | None] = (0.0, None)
    for existing in entries:
        validated = validate_entry(existing)
        if entries_match(validated, new_entry):
            return validated
        old_sig = signature_text(str(validated["error"]), str(validated["lesson"]))
        score = fuzzy_similarity(new_sig, old_sig)
        if score > best[0]:
            best = (score, validated)
    if best[1] is not None and best[0] >= threshold:
        return best[1]
    return None


def discover_memory_paths(workspace_root: Path) -> list[Path]:
    """Resolve MEMORY.md and daily memory/*.md paths (same layout as memory_cleanup)."""

    try:
        from scripts.memory_cleanup import discover_memory_files
    except ImportError:
        main = workspace_root / "MEMORY.md"
        nested = workspace_root / "memory" / "MEMORY.md"
        main_memory = main if main.exists() else nested if nested.exists() else None
        daily_dir = workspace_root / "memory"
        daily: list[Path] = []
        if daily_dir.is_dir():
            for path in sorted(daily_dir.glob("*.md")):
                if path.name == "MEMORY.md" or ".backup_" in path.name:
                    continue
                daily.append(path)
        return [p for p in [main_memory, *daily] if p is not None]

    main_memory, daily_files = discover_memory_files(workspace_root)
    return [p for p in [main_memory, *daily_files] if p is not None]


def _chunk_markdown_by_heading(text: str) -> list[str]:
    """Split markdown into sections on ## headings for finer-grained matching."""

    lines = text.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    heading_re = re.compile(r"^\s{0,3}##+\s+")
    for line in lines:
        if heading_re.match(line) and current:
            chunks.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append("\n".join(current).strip())
    return [c for c in chunks if c]


def iter_memory_chunks(workspace_root: Path) -> list[tuple[Path, str]]:
    """Load searchable text chunks from memory files to cross-check lessons."""

    out: list[tuple[Path, str]] = []
    for path in discover_memory_paths(workspace_root):
        try:
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size > MAX_MEMORY_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sections = _chunk_markdown_by_heading(text)
        if len(sections) <= 1:
            out.append((path, normalize_text(text)))
        else:
            for section in sections:
                nt = normalize_text(section)
                if len(nt) >= 40:
                    out.append((path, nt))
    return out


def memory_match_for_lesson(
    lesson: str,
    workspace_root: Path,
    *,
    min_ratio: float = 0.82,
) -> list[tuple[Path, float]]:
    """Return memory chunks whose text is similar to the lesson (sorted best-first)."""

    needle = normalize_text(lesson)
    if len(needle) < 12:
        return []
    ranked: list[tuple[Path, float]] = []
    for path, chunk in iter_memory_chunks(workspace_root):
        ratio = fuzzy_similarity(needle, chunk)
        if ratio >= min_ratio:
            ranked.append((path, ratio))
    ranked.sort(key=lambda item: (-item[1], item[0].as_posix()))
    deduped: list[tuple[Path, float]] = []
    seen_paths: set[str] = set()
    for path, ratio in ranked:
        key = path.as_posix()
        if key in seen_paths:
            continue
        seen_paths.add(key)
        deduped.append((path, ratio))
    return deduped[:5]


def default_markdown_preamble() -> str:
    """Initial human-editable section before the auto-generated block."""

    return (
        "# OpenClaw error patterns\n\n"
        "Companion log for `error_log.json`. Edit **Recent patterns** freely; "
        f"the section between `{MARKDOWN_AUTO_BEGIN}` and `{MARKDOWN_AUTO_END}` "
        "is rewritten when you run `add` or `sync-md`.\n\n"
        "## Recent patterns\n\n"
        "- **Parser / JSON**: Truncated or invalid structured output — validate, chunk, "
        "and retry with stricter output constraints.\n"
        "- **Timeouts**: Long sessions or hung tool calls — shorten prompts, add checkpoints, "
        "and enforce deadlines.\n"
        "- **Network / API**: Connection drops and rate limits — exponential backoff, "
        "smaller batches, and idempotent retries.\n"
        "- **Git**: Push conflicts and auth — pull/rebase first, verify remotes and tokens.\n"
        "\n"
    )


def split_markdown_for_sync(text: str) -> tuple[str, str]:
    """Separate manual preamble from auto block; missing markers => (full text, '')."""

    if MARKDOWN_AUTO_BEGIN in text and MARKDOWN_AUTO_END in text:
        before, rest = text.split(MARKDOWN_AUTO_BEGIN, 1)
        auto, _after = rest.split(MARKDOWN_AUTO_END, 1)
        return before.rstrip() + "\n\n", auto.strip()
    return text.rstrip() + "\n\n", ""


def render_auto_markdown(entries: list[dict[str, object]]) -> str:
    """Build the auto-maintained markdown body."""

    lines: list[str] = [
        f"{MARKDOWN_AUTO_BEGIN}",
        "",
        f"_Last sync: {datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')}_",
        "",
        "## Automation rule candidates",
        "",
        "*(Categories with ≥2 entries — use as cron / agent guardrails.)*",
        "",
    ]
    inferred_by_cat: dict[str, list[dict[str, object]]] = {}
    for entry in entries:
        cat = infer_error_category(str(entry["error"]), str(entry["lesson"]))
        inferred_by_cat.setdefault(cat, []).append(entry)
    had_repeat_category = False
    for cat in sorted(inferred_by_cat.keys(), key=lambda c: (-len(inferred_by_cat[c]), c)):
        bucket = inferred_by_cat[cat]
        if len(bucket) < 2:
            continue
        had_repeat_category = True
        lines.append(f"- **{cat}** ({len(bucket)}×): prefer lessons that mention: ")
        lessons_preview = "; ".join(normalize_text(str(e["lesson"]))[:120] for e in bucket[:3])
        lines.append(f"  {lessons_preview}")
    if not had_repeat_category:
        lines.append("- _(No category yet has enough repeats — add more learnings.)_")
    lines.extend(["", "## Entries (newest first)", ""])
    for entry in sorted(entries, key=lambda e: str(e["timestamp"]), reverse=True):
        eid = entry["id"]
        cat = str(entry["category"])
        status = "resolved" if entry["resolved"] else "open"
        inf = infer_error_category(str(entry["error"]), str(entry["lesson"]))
        lines.append(f"### `{eid}` — {cat} [{status}] _(inferred: {inf})_")
        lines.append("")
        lines.append(f"- **When:** {entry['timestamp']}")
        lines.append(f"- **Error:** {entry['error']}")
        lines.append(f"- **Lesson:** {entry['lesson']}")
        lines.append("")
        lines.append("---")
        lines.append("")
    lines.append(MARKDOWN_AUTO_END)
    lines.append("")
    return "\n".join(lines)


def sync_markdown_log(
    md_path: Path,
    entries: list[dict[str, object]],
    *,
    preamble: str | None = None,
) -> None:
    """Write or refresh `error_log.md`, preserving manual preamble inside markers."""

    md_path.parent.mkdir(parents=True, exist_ok=True)
    if md_path.exists():
        existing = md_path.read_text(encoding="utf-8")
        manual, _old_auto = split_markdown_for_sync(existing)
        base = manual if manual.strip() else default_markdown_preamble()
    else:
        base = preamble or default_markdown_preamble()
    body = render_auto_markdown(entries)
    md_path.write_text(base.rstrip() + "\n\n" + body, encoding="utf-8")


def dedupe_entries(entries: list[dict[str, object]], *, threshold: float) -> tuple[list[dict[str, object]], list[str]]:
    """
    Collapse near-duplicate entries (keeps first occurrence by timestamp desc).
    Returns (new list, human-readable merge notes).
    """

    sorted_entries = sorted(entries, key=lambda e: str(e["timestamp"]), reverse=True)
    kept: list[dict[str, object]] = []
    notes: list[str] = []
    for entry in sorted_entries:
        dup = find_near_duplicate(entry, kept, threshold=threshold)
        if dup:
            notes.append(
                f"Dropped near-duplicate of {dup['id']}: {normalize_text(str(entry['error']))[:80]}"
            )
            continue
        kept.append(validate_entry(entry))
    return kept, notes


def add_entry(
    log_path: Path,
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
    markdown_path: Path | None = None,
    workspace_root: Path | None = None,
    near_duplicate_threshold: float = DEFAULT_NEAR_DUPLICATE_THRESHOLD,
    check_memory: bool = True,
) -> tuple[dict[str, object], bool, list[str], str]:
    """
    Add an error learning entry unless it already exists (exact or fuzzy).

    Returns (entry, created, side_channel_messages, duplicate_kind).
    duplicate_kind is 'new', 'exact', or 'near'.
    """

    ws = workspace_root or DEFAULT_WORKSPACE_ROOT
    messages: list[str] = []
    store = load_store(log_path)
    new_entry = build_entry(category, error, lesson, resolved=resolved)
    entries = store["entries"]
    assert isinstance(entries, list)

    validated_list = [validate_entry(e) for e in entries]
    near = find_near_duplicate(new_entry, validated_list, threshold=near_duplicate_threshold)
    if near is not None and not entries_match(near, new_entry):
        messages.append(
            f"Near-duplicate of entry {near['id']} (similarity ≥ {near_duplicate_threshold:.2f}); kept existing."
        )
        if check_memory:
            for path, ratio in memory_match_for_lesson(lesson, ws):
                messages.append(f"Memory overlap ({ratio:.2f}): {path}")
        return near, False, messages, "near"
    for entry in validated_list:
        if entries_match(entry, new_entry):
            if check_memory:
                for path, ratio in memory_match_for_lesson(lesson, ws):
                    messages.append(f"Memory overlap ({ratio:.2f}): {path}")
            return entry, False, messages, "exact"

    if check_memory:
        for path, ratio in memory_match_for_lesson(lesson, ws):
            messages.append(f"Similar lesson already in memory ({ratio:.2f}): {path}")

    entries.append(new_entry)
    entries.sort(key=lambda item: str(item["timestamp"]), reverse=True)
    save_store(log_path, store)
    md_target = markdown_path if markdown_path is not None else log_path.with_suffix(".md")
    sync_markdown_log(md_target, [validate_entry(e) for e in entries])
    return new_entry, True, messages, "new"


def format_entry(entry: dict[str, object]) -> str:
    """Render a single entry for console output."""

    category = str(entry["category"])
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    inferred = infer_error_category(str(entry["error"]), str(entry["lesson"]))
    lines = [
        (
            f"{colorize(category, category_color(category))} "
            f"{colorize(f'[{status_text}]', status_color)} "
            f"{colorize(str(entry['timestamp']), 'cyan')}"
        ),
        f"  {colorize('ID:', 'yellow')} {entry['id']}",
        f"  {colorize('Inferred type:', 'cyan')} {inferred}",
        f"  {colorize('Error:', 'red')} {entry['error']}",
        f"  {colorize('Lesson:', 'green')} {entry['lesson']}",
    ]
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

    raw_counts = Counter(str(entry["category"]) for entry in entries)
    inferred_counts = Counter(
        infer_error_category(str(entry["error"]), str(entry["lesson"])) for entry in entries
    )
    total = len(entries)
    print(colorize("Recorded categories (as logged):", "bold"))
    for category, count in sorted(raw_counts.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (count / total) * 100
        print(
            f"- {colorize(category, category_color(category))}: "
            f"{colorize(str(count), 'red')} "
            f"({share:.1f}%)"
        )
    print()
    print(colorize("Inferred error-type buckets (keyword rules):", "bold"))
    for category, count in sorted(inferred_counts.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (count / total) * 100
        print(
            f"- {colorize(category, category_color(category))}: "
            f"{colorize(str(count), 'red')} "
            f"({share:.1f}%)"
        )


def search_score(query: str, entry: dict[str, object]) -> float:
    """Score how relevant an entry is to a search query."""

    normalized_query = normalize_text(query)
    haystack = normalize_text(
        " ".join(
            (
                str(entry["category"]),
                str(entry["error"]),
                str(entry["lesson"]),
                infer_error_category(str(entry["error"]), str(entry["lesson"])),
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
    """Return the most relevant matching entries for the given query."""

    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = search_score(query, entry)
        if score >= 0.45:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])), reverse=False)
    return [entry for _, entry in ranked[:limit]]


def print_automation_rules(entries: list[dict[str, object]], *, min_count: int = 2) -> None:
    """Print suggested guardrail rules for recurring inferred categories."""

    print(colorize("Suggested automation rules", "bold"))
    print(colorize("==========================", "cyan"))
    if len(entries) < min_count:
        print(colorize("Not enough entries to derive rules.", "yellow"))
        return

    by_cat: dict[str, list[dict[str, object]]] = {}
    for entry in entries:
        cat = infer_error_category(str(entry["error"]), str(entry["lesson"]))
        by_cat.setdefault(cat, []).append(entry)

    printed = False
    for cat in sorted(by_cat.keys(), key=lambda c: (-len(by_cat[c]), c)):
        bucket = by_cat[cat]
        if len(bucket) < min_count:
            continue
        printed = True
        print()
        print(colorize(f"Rule: when errors look like [{cat}] ({len(bucket)} hits)", "yellow"))
        print("  Trigger keywords / conditions:")
        for needle, mapped in ERROR_TYPE_KEYWORD_RULES:
            if mapped == cat:
                print(f"    - contains `{needle}`")
        # Most recent lesson as default playbook line
        latest = max(bucket, key=lambda e: str(e["timestamp"]))
        print("  Default playbook:")
        print(f"    - {latest['lesson']}")
        print("  Automation ideas:")
        print("    - Add a pre-flight check script or CI step that scans logs for these keywords.")
        print("    - Downgrade to smaller batches / shorter timeouts when the pattern appears twice in 24h.")
    if not printed:
        print(colorize("No inferred category repeats yet; log more failures or lower --min-count.", "yellow"))


def print_memory_report(entries: list[dict[str, object]], workspace_root: Path) -> None:
    """List learnings that closely match existing memory files."""

    print(colorize("Memory cross-check (lessons similar to MEMORY.md / memory/*.md)", "bold"))
    print(colorize("=================================================================", "cyan"))
    hits = 0
    for entry in entries:
        matches = memory_match_for_lesson(str(entry["lesson"]), workspace_root)
        if not matches:
            continue
        hits += 1
        print()
        print(format_entry(entry))
        for path, ratio in matches[:3]:
            print(f"  {colorize('Similar memory:', 'cyan')} {path} ({ratio:.2f})")
    if not hits:
        print(colorize("No strong overlaps found (or no memory files present).", "yellow"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(description="Capture and learn from OpenClaw errors.")
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to the error learning JSON log.",
    )
    parser.add_argument(
        "--markdown-path",
        type=Path,
        default=None,
        help="Companion markdown log (default: same path as JSON with .md extension).",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=DEFAULT_WORKSPACE_ROOT,
        help="Workspace root for memory file discovery (MEMORY.md, memory/*.md).",
    )
    parser.add_argument(
        "--near-duplicate-threshold",
        type=float,
        default=DEFAULT_NEAR_DUPLICATE_THRESHOLD,
        help="0..1 similarity for fuzzy deduplication on error+lesson text.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new error learning entry.")
    add_parser.add_argument("error_category", help="High-level category for the error (use 'auto' to infer).")
    add_parser.add_argument("error_message", help="Error message or failure summary.")
    add_parser.add_argument("lesson_learned", help="Lesson learned from the failure.")
    add_parser.add_argument(
        "--infer-category",
        action="store_true",
        help="Override category using keyword rules (same as passing 'auto').",
    )
    add_parser.add_argument(
        "--no-memory-check",
        dest="memory_check",
        action="store_false",
        default=True,
        help="Skip similarity check against memory markdown files.",
    )
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
    subparsers.add_parser("sync-md", help="Refresh markdown log from JSON without adding entries.")
    subparsers.add_parser("memory-check", help="Show entries whose lesson overlaps memory files.")

    rules_parser = subparsers.add_parser("rules", help="Print automation ideas for recurring error types.")
    rules_parser.add_argument(
        "--min-count",
        type=int,
        default=2,
        help="Minimum occurrences of an inferred category to emit a rule.",
    )

    dedupe_parser = subparsers.add_parser(
        "dedupe",
        help="Remove near-duplicate entries from the JSON log (keeps newest).",
    )

    search_parser = subparsers.add_parser("search", help="Search for relevant past errors.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of matching entries to print.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the error learning CLI."""

    args = parse_args(argv)
    markdown_path = args.markdown_path or args.log_path.with_suffix(".md")
    try:
        store = load_store(args.log_path)
    except ErrorLearningError as exc:
        print(colorize(str(exc), "red"), file=sys.stderr)
        return 1

    entries = store["entries"]
    assert isinstance(entries, list)

    if args.command == "add":
        category = args.error_category.strip()
        if args.infer_category or category.lower() == "auto":
            category = infer_error_category(args.error_message, args.lesson_learned)
        try:
            entry, created, side_messages, dup_kind = add_entry(
                args.log_path,
                category,
                args.error_message,
                args.lesson_learned,
                resolved=args.resolved,
                markdown_path=markdown_path,
                workspace_root=args.workspace_root,
                near_duplicate_threshold=max(0.0, min(1.0, args.near_duplicate_threshold)),
                check_memory=args.memory_check,
            )
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1

        for line in side_messages:
            print(colorize(line, "yellow"))
        if created:
            print(colorize("Saved error learning entry.", "green"))
        elif dup_kind == "exact":
            print(colorize("Duplicate entry detected; existing learning kept.", "yellow"))
        else:
            print(colorize("Near-duplicate detected; existing learning kept.", "yellow"))
        print(format_entry(entry))
        return 0

    validated_entries = [validate_entry(entry) for entry in entries]

    if args.command == "list":
        print_entries(validated_entries, heading="OpenClaw Error Learnings")
        return 0

    if args.command == "stats":
        print_stats(validated_entries)
        return 0

    if args.command == "search":
        matches = search_entries(validated_entries, args.query, limit=max(args.limit, 1))
        print_entries(matches, heading=f"Search Results: {args.query}")
        return 0

    if args.command == "sync-md":
        sync_markdown_log(markdown_path, validated_entries)
        print(colorize(f"Markdown log updated: {markdown_path}", "green"))
        return 0

    if args.command == "memory-check":
        print_memory_report(validated_entries, args.workspace_root)
        return 0

    if args.command == "rules":
        print_automation_rules(validated_entries, min_count=max(1, args.min_count))
        return 0

    if args.command == "dedupe":
        threshold = max(0.0, min(1.0, args.near_duplicate_threshold))
        new_list, notes = dedupe_entries(validated_entries, threshold=threshold)
        removed = len(validated_entries) - len(new_list)
        if removed:
            store["entries"] = new_list
            save_store(args.log_path, store)
            sync_markdown_log(markdown_path, new_list)
        for line in notes:
            print(colorize(line, "cyan"))
        print(
            colorize(
                f"Dedupe complete: removed {removed} near-duplicate(s); {len(new_list)} entr(y/ies) remain.",
                "green" if removed else "yellow",
            )
        )
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
