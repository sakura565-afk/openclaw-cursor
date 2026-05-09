#!/usr/bin/env python3
"""Capture and learn from recurring OpenClaw session errors."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path


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

# Canonical kinds inferred from category + error text (stored as category_kind).
CATEGORY_KIND_KEYWORDS: dict[str, tuple[str, ...]] = {
    "tool_execution": ("tool", "mcp", "subprocess", "exit code", "command failed", "errno"),
    "network_io": (
        "timeout",
        "timed out",
        "connection",
        "econnrefused",
        "enotfound",
        "network",
        "socket",
        "ssl",
        "tls",
        "fetch",
        "http ",
        "503",
        "502",
        "429",
    ),
    "parse_format": (
        "parser",
        "parse",
        "json",
        "yaml",
        "xml",
        "syntax",
        "invalid format",
        "malformed",
        "truncated",
        "unterminated",
    ),
    "auth_permission": ("auth", "permission", "forbidden", "401", "403", "unauthorized", "credential", "token"),
    "resource_limits": ("memory", "oom", "disk", "quota", "rate limit", "too large", "context length"),
    "logic_runtime": ("runtime", "exception", "traceback", "panic", "crash", "null", "undefined", "assert"),
    "config_environment": ("config", "env", "missing variable", "setting", "path not found", "module not found"),
    "workflow_session": ("session", "openclaw", "checkpoint", "prompt", "agent", "deadlock", "stuck"),
}

ACTION_VERBS = frozenset(
    """
    add fix validate retry verify ensure disable enable wrap chunk normalize purge
    refactor guard check update replace migrate configure install upgrade downgrade
    rollback isolate document test mock stub cache backoff throttle queue split
    merge dedupe pin lock unlock rotate renew refresh rebuild redeploy
    """.split()
)

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
_HEX_RUN_RE = re.compile(r"\b0x[0-9a-f]+\b", re.I)
_LINE_COL_RE = re.compile(r"(line)\s+\d+", re.I)
_NUMERIC_TOKEN_RE = re.compile(r"\b\d+\b")


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


def infer_category_kind(category: str, error: str) -> str:
    """Map free-form category and error text to a stable taxonomy label."""

    blob = normalize_text(f"{category} {error}")
    best_kind = "uncategorized"
    best_hits = 0
    for kind, keywords in CATEGORY_KIND_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in blob)
        if hits > best_hits:
            best_hits = hits
            best_kind = kind
    return best_kind if best_hits else "uncategorized"


def normalize_error_signature(error: str) -> str:
    """Strip volatile tokens so similar failures cluster into one pattern."""

    text = normalize_text(error)
    text = _UUID_RE.sub("<uuid>", text)
    text = _HEX_RUN_RE.sub("<hex>", text)
    text = re.sub(r"(/[^\s]+)|([a-z]:\\[^\s]+)", "<path>", text, flags=re.I)
    text = _LINE_COL_RE.sub(r"\1 <n>", text)
    text = _NUMERIC_TOKEN_RE.sub("<n>", text)
    return " ".join(text.split())


def compute_pattern_key(category: str, error: str) -> str:
    """Stable key for recurrence grouping (category + normalized error shape)."""

    sig = normalize_error_signature(error)
    raw = f"{normalize_text(category)}|{sig}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def score_fix_quality(entry: dict[str, object]) -> float:
    """Heuristic 0–10 score: specificity, actionability, resolution, and recurrence."""

    lesson = normalize_text(str(entry.get("lesson", "")))
    lesson_tokens = set(lesson.split())
    error_text = normalize_text(str(entry.get("error", "")))

    score = 0.0
    if len(lesson) >= 12:
        score += 1.5
    if len(lesson) >= 40:
        score += 1.0

    action_hits = len(lesson_tokens & ACTION_VERBS)
    score += min(2.5, action_hits * 0.45)

    if any(ch in lesson for ch in (":", ";", "`", '"', "'", ",")) or "\n" in str(entry.get("lesson", "")):
        score += 0.5

    overlap = len(set(error_text.split()) & lesson_tokens)
    score += min(2.0, overlap * 0.25)

    if bool(entry.get("resolved")):
        score += 1.0
    else:
        score -= 0.5

    occ = int(entry.get("occurrence_count", 1) or 1)
    if occ > 1:
        score += min(1.5, 0.35 * (occ - 1))
        if bool(entry.get("resolved")):
            score += 0.5

    fa = int(entry.get("failed_fix_attempts", 0) or 0)
    score -= min(2.0, fa * 0.4)

    return max(0.0, min(10.0, round(score, 1)))


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
    occurrence_count: int = 1,
    failed_fix_attempts: int = 0,
    category_kind: str | None = None,
    pattern_key: str | None = None,
) -> dict[str, object]:
    """Create a log entry that matches the JSON schema."""

    payload = canonical_payload(category, error, lesson, resolved)
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    created_at = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    kind = category_kind or infer_category_kind(category, error)
    pkey = pattern_key or compute_pattern_key(category, error)
    return {
        "id": digest,
        "timestamp": created_at,
        "category": category.strip(),
        "error": error.strip(),
        "lesson": lesson.strip(),
        "resolved": bool(resolved),
        "category_kind": kind,
        "pattern_key": pkey,
        "occurrence_count": max(1, int(occurrence_count)),
        "failed_fix_attempts": max(0, int(failed_fix_attempts)),
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

    occ = entry.get("occurrence_count", 1)
    if not isinstance(occ, int) or isinstance(occ, bool) or occ < 1:
        occ = 1
    entry["occurrence_count"] = occ

    fa = entry.get("failed_fix_attempts", 0)
    if not isinstance(fa, int) or isinstance(fa, bool) or fa < 0:
        fa = 0
    entry["failed_fix_attempts"] = fa

    cat = str(entry["category"])
    err = str(entry["error"])
    if not isinstance(entry.get("category_kind"), str) or not str(entry["category_kind"]).strip():
        entry["category_kind"] = infer_category_kind(cat, err)
    if not isinstance(entry.get("pattern_key"), str) or not str(entry["pattern_key"]).strip():
        entry["pattern_key"] = compute_pattern_key(cat, err)

    if not isinstance(entry.get("id"), str) or not entry["id"].strip():
        entry["id"] = build_entry(
            cat,
            err,
            str(entry["lesson"]),
            resolved=resolved,
            timestamp=str(entry["timestamp"]),
            occurrence_count=occ,
            failed_fix_attempts=fa,
            category_kind=str(entry["category_kind"]),
            pattern_key=str(entry["pattern_key"]),
        )["id"]
    entry["resolved"] = resolved
    return entry


def migrate_store(store: dict[str, object]) -> dict[str, object]:
    """Upgrade older on-disk documents to the current schema."""

    ver = int(store.get("schema_version", SCHEMA_VERSION))
    entries = store.get("entries", [])
    if not isinstance(entries, list):
        raise ErrorLearningError("Error log field 'entries' must be a JSON array.")
    normalized_entries = [validate_entry(item) for item in entries]
    store["entries"] = normalized_entries
    if ver < SCHEMA_VERSION:
        store["schema_version"] = SCHEMA_VERSION
    return store


def load_store(log_path: Path) -> dict[str, object]:
    """Load the persisted error log from disk."""

    if not log_path.exists():
        return default_store()

    try:
        raw = json.loads(log_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErrorLearningError(f"Unable to parse JSON from {log_path}: {exc}") from exc

    if isinstance(raw, list):
        store = {"schema_version": SCHEMA_VERSION, "entries": raw}
        return migrate_store(store)

    if not isinstance(raw, dict):
        raise ErrorLearningError("Error log must contain a JSON object or list of entries.")

    raw_entries = raw.get("entries", [])
    if not isinstance(raw_entries, list):
        raise ErrorLearningError("Error log field 'entries' must be a JSON array.")

    store = {
        "schema_version": int(raw.get("schema_version", SCHEMA_VERSION)),
        "entries": raw_entries,
    }
    return migrate_store(store)


def save_store(log_path: Path, store: dict[str, object]) -> None:
    """Persist the error log to disk with retries on transient OS errors."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    store = migrate_store(dict(store))
    payload = json.dumps(store, indent=2) + "\n"
    last_exc: OSError | None = None
    for attempt in range(5):
        try:
            log_path.write_text(payload, encoding="utf-8")
            return
        except OSError as exc:
            last_exc = exc
            time.sleep(0.05 * (2**attempt))
    assert last_exc is not None
    raise ErrorLearningError(f"Unable to write error log after retries: {last_exc}") from last_exc


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


def add_entry(
    log_path: Path,
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
    category_kind: str | None = None,
    record_failed_attempt: bool = False,
) -> tuple[dict[str, object], bool]:
    """Add an error learning entry unless it already exists.

    Exact duplicates bump ``occurrence_count``. When ``record_failed_attempt`` is True
    (typical after a fix did not stick), merge path increments ``failed_fix_attempts``.
    """

    store = load_store(log_path)
    new_entry = build_entry(
        category,
        error,
        lesson,
        resolved=resolved,
        category_kind=category_kind,
    )
    entries = store["entries"]
    assert isinstance(entries, list)
    for index, entry in enumerate(entries):
        validated = validate_entry(entry)
        if entries_match(validated, new_entry):
            prev_occ = int(validated.get("occurrence_count", 1) or 1)
            prev_fa = int(validated.get("failed_fix_attempts", 0) or 0)
            merged = dict(validated)
            merged["occurrence_count"] = prev_occ + 1
            if record_failed_attempt or not resolved:
                merged["failed_fix_attempts"] = prev_fa + 1
            merged["timestamp"] = str(new_entry["timestamp"])
            merged["pattern_key"] = str(new_entry["pattern_key"])
            merged["category_kind"] = str(new_entry["category_kind"])
            entries[index] = merged
            entries.sort(key=lambda item: str(item["timestamp"]), reverse=True)
            save_store(log_path, store)
            return merged, False

    entries.append(new_entry)
    entries.sort(key=lambda item: str(item["timestamp"]), reverse=True)
    save_store(log_path, store)
    return new_entry, True


def format_entry(entry: dict[str, object]) -> str:
    """Render a single entry for console output."""

    entry = validate_entry(entry)
    category = str(entry["category"])
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    occ = int(entry.get("occurrence_count", 1) or 1)
    fa = int(entry.get("failed_fix_attempts", 0) or 0)
    fq = score_fix_quality(entry)
    lines = [
        (
            f"{colorize(category, category_color(category))} "
            f"{colorize(f'[{status_text}]', status_color)} "
            f"{colorize(str(entry['timestamp']), 'cyan')}"
        ),
        f"  {colorize('ID:', 'yellow')} {entry['id']}  "
        f"{colorize('pattern:', 'cyan')} {entry['pattern_key']}  "
        f"{colorize('kind:', 'yellow')} {entry['category_kind']}",
        f"  {colorize('Quality:', 'green')} {fq}/10  "
        f"{colorize('×', 'red')} {occ}"
        + (f"  {colorize('failed fixes:', 'red')} {fa}" if fa else ""),
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


def aggregate_patterns(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    """Roll up entries by pattern_key for recurrence visibility."""

    by_key: dict[str, dict[str, object]] = {}
    for raw in entries:
        e = validate_entry(raw)
        key = str(e["pattern_key"])
        prev = by_key.get(key)
        occ = int(e.get("occurrence_count", 1) or 1)
        fa = int(e.get("failed_fix_attempts", 0) or 0)
        fq = score_fix_quality(e)
        if prev is None:
            by_key[key] = {
                "pattern_key": key,
                "category_kind": e["category_kind"],
                "sample_category": e["category"],
                "signature": normalize_error_signature(str(e["error"])),
                "total_occurrences": occ,
                "total_failed_fix_attempts": fa,
                "entry_count": 1,
                "avg_quality": fq,
                "open_count": 0 if e["resolved"] else 1,
            }
        else:
            prev["total_occurrences"] = int(prev["total_occurrences"]) + occ
            prev["total_failed_fix_attempts"] = int(prev["total_failed_fix_attempts"]) + fa
            prev["entry_count"] = int(prev["entry_count"]) + 1
            prev["avg_quality"] = round(
                (float(prev["avg_quality"]) * (int(prev["entry_count"]) - 1) + fq) / int(prev["entry_count"]),
                2,
            )
            if not e["resolved"]:
                prev["open_count"] = int(prev["open_count"]) + 1
    rows = list(by_key.values())
    rows.sort(key=lambda r: (-int(r["total_occurrences"]), -int(r["entry_count"])))
    return rows


def trend_series(
    entries: list[dict[str, object]],
    *,
    granularity: str,
) -> tuple[list[tuple[str, int]], dict[str, Counter[str]]]:
    """Bucket entry timestamps for volume trends and kind mix."""

    buckets: Counter[str] = Counter()
    kind_per_bucket: dict[str, Counter[str]] = defaultdict(Counter)

    for raw in entries:
        e = validate_entry(raw)
        ts_raw = str(e["timestamp"])
        try:
            if ts_raw.endswith("Z"):
                dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if granularity == "day":
            label = dt.strftime("%Y-%m-%d")
        else:
            iso = dt.isocalendar()
            label = f"{iso.year}-W{iso.week:02d}"
        weight = int(e.get("occurrence_count", 1) or 1)
        buckets[label] += weight
        kind_per_bucket[label][str(e["category_kind"])] += weight

    ordered = sorted(buckets.items(), key=lambda x: x[0])
    return ordered, dict(kind_per_bucket)


def trend_velocity(series: list[tuple[str, int]]) -> str:
    """Compare recent bucket total to prior bucket for a quick trajectory label."""

    if len(series) < 2:
        return "insufficient history"
    recent = series[-1][1]
    prior = series[-2][1]
    if prior == 0:
        return "up" if recent > 0 else "flat"
    delta = (recent - prior) / prior
    if delta > 0.15:
        return "worsening"
    if delta < -0.15:
        return "improving"
    return "stable"


def print_patterns(entries: list[dict[str, object]], *, limit: int) -> None:
    """Print grouped recurrence patterns."""

    print(colorize("Recurring Error Patterns", "bold"))
    print(colorize("========================", "cyan"))
    rows = aggregate_patterns(entries)
    if not rows:
        print(colorize("No entries found.", "yellow"))
        return
    for row in rows[:limit]:
        pk = row["pattern_key"]
        print(
            f"- {colorize(pk, 'yellow')}  "
            f"{colorize(str(row['category_kind']), category_color(str(row['category_kind'])))}  "
            f"occurrences={colorize(str(row['total_occurrences']), 'red')}  "
            f"entries={row['entry_count']}  "
            f"avg_quality={row['avg_quality']}"
        )
        if int(row.get("total_failed_fix_attempts", 0) or 0) > 0:
            print(
                f"    {colorize('failed fix attempts (rolled up):', 'red')} "
                f"{row['total_failed_fix_attempts']}"
            )
        print(f"    sample category: {row['sample_category']}")
        print(f"    signature: {row['signature'][:160]}{'…' if len(str(row['signature'])) > 160 else ''}")


def print_trends(entries: list[dict[str, object]], *, granularity: str) -> None:
    """Print time-bucketed volume and trajectory."""

    print(colorize(f"Error Trends ({granularity})", "bold"))
    print(colorize("=" * (14 + len(granularity)), "cyan"))
    series, kinds = trend_series(entries, granularity=granularity)
    if not series:
        print(colorize("No dated entries to analyze.", "yellow"))
        return
    for label, count in series[-12:]:
        mix = kinds.get(label, Counter())
        top_kinds = ", ".join(f"{k}={v}" for k, v in mix.most_common(3))
        extra = f"  ({top_kinds})" if top_kinds else ""
        print(f"  {colorize(label, 'cyan')}: {colorize(str(count), 'red')}{extra}")
    vel = trend_velocity(series)
    print()
    print(
        f"{colorize('Trajectory (latest vs prior bucket):', 'bold')} "
        f"{colorize(vel, 'yellow' if vel == 'stable' else ('red' if vel == 'worsening' else 'green'))}"
    )


def print_stats(entries: list[dict[str, object]]) -> None:
    """Print category-level frequency stats and quick pattern snapshot."""

    print(colorize("Error Learning Stats", "bold"))
    print(colorize("====================", "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    validated = [validate_entry(e) for e in entries]
    counts = Counter(str(e["category"]) for e in validated)
    kind_counts: Counter[str] = Counter()
    for e in validated:
        kind_counts[str(e["category_kind"])] += int(e.get("occurrence_count", 1) or 1)
    total = sum(int(e.get("occurrence_count", 1) or 1) for e in validated)

    print(colorize("By user category:", "bold"))
    for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())):
        raw_share = (count / max(len(validated), 1)) * 100
        print(
            f"- {colorize(category, category_color(category))}: "
            f"{colorize(str(count), 'red')} entries "
            f"({raw_share:.1f}% of rows)"
        )

    print()
    print(colorize("By inferred kind (weighted by occurrences):", "bold"))
    kind_total = sum(kind_counts.values()) or 1
    for kind, weighted in sorted(kind_counts.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (weighted / kind_total) * 100
        print(
            f"- {colorize(kind, category_color(kind))}: "
            f"{colorize(str(weighted), 'red')} weighted ({share:.1f}%)"
        )

    print()
    print(f"{colorize('Total weighted occurrences:', 'bold')} {colorize(str(total), 'red')}")

    patterns = aggregate_patterns(validated)
    hot = [p for p in patterns if int(p["total_occurrences"]) > 1 or int(p["entry_count"]) > 1]
    if hot:
        print()
        print(colorize("Hot patterns (need attention):", "bold"))
        for row in hot[:5]:
            print(
                f"  · {row['pattern_key']}  "
                f"{row['category_kind']}  ×{row['total_occurrences']}"
            )

    series, _ = trend_series(validated, granularity="week")
    if len(series) >= 2:
        print()
        print(
            f"{colorize('Weekly trajectory:', 'bold')} "
            f"{colorize(trend_velocity(series), 'cyan')}"
        )


def search_score(query: str, entry: dict[str, object]) -> float:
    """Score how relevant an entry is to a search query."""

    normalized_query = normalize_text(query)
    entry = validate_entry(entry)
    haystack = normalize_text(
        " ".join(
            (
                str(entry["category"]),
                str(entry["error"]),
                str(entry["lesson"]),
                str(entry["category_kind"]),
                str(entry["pattern_key"]),
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
    quality_boost = score_fix_quality(entry) / 40.0
    return substring_bonus + overlap + (ratio * 0.5) + quality_boost


def search_entries(entries: list[dict[str, object]], query: str, limit: int = 10) -> list[dict[str, object]]:
    """Return the most relevant matching entries for the given query."""

    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = search_score(query, entry)
        if score >= 0.45:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])))
    return [entry for _, entry in ranked[:limit]]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(description="Capture and learn from OpenClaw errors.")
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
    add_parser.add_argument(
        "--kind",
        dest="category_kind",
        default=None,
        help="Override inferred category_kind (e.g. network_io, parse_format).",
    )
    add_parser.add_argument(
        "--failed-fix",
        action="store_true",
        help="Count this add as a failed fix attempt when merging with an existing identical entry.",
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

    search_parser = subparsers.add_parser("search", help="Search for relevant past errors.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of matching entries to print.",
    )

    patterns_parser = subparsers.add_parser("patterns", help="Show recurring errors grouped by pattern key.")
    patterns_parser.add_argument(
        "--limit",
        type=int,
        default=15,
        help="Maximum number of pattern groups to print.",
    )

    trends_parser = subparsers.add_parser("trends", help="Show occurrence trends over time.")
    trends_parser.add_argument(
        "--granularity",
        choices=("day", "week"),
        default="week",
        help="Time bucket size (default: week).",
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
                category_kind=args.category_kind,
                record_failed_attempt=bool(args.failed_fix),
            )
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1

        if created:
            print(colorize("Saved error learning entry.", "green"))
        else:
            msg = "Repeat occurrence merged into existing learning"
            if args.failed_fix or not args.resolved:
                msg += " (failed fix attempt recorded)"
            print(colorize(f"{msg}; occurrence_count updated.", "yellow"))
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

    if args.command == "patterns":
        print_patterns(validated_entries, limit=max(args.limit, 1))
        return 0

    if args.command == "trends":
        print_trends(validated_entries, granularity=args.granularity)
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
