#!/usr/bin/env python3
"""Capture and learn from recurring OpenClaw session errors.

Features: pattern clustering, heuristic root-cause hints, automatic ingest from
the self-improvement engine logs (``auto_improvements_*.json``), deduplication
by semantic fingerprint, occurrence tracking, and actionable insight reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = ROOT / ".learnings" / "error_log.json"
DEFAULT_IMPROVEMENT_LOG_DIR = ROOT / "logs"
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

# Heuristic keywords for lightweight root-cause tagging (no external ML).
_PATTERN_RULES: tuple[tuple[str, frozenset[str]], ...] = (
    ("timeout", frozenset({"timeout", "timed out", "deadline", "etimedout"})),
    ("network", frozenset({"connection refused", "econnrefused", "network", "unreachable", "dns", "socket"})),
    ("disk_space", frozenset({"disk", "no space", "enospc", "space low", "full disk"})),
    ("memory", frozenset({"oom", "out of memory", "cannot allocate", "memory", "enOMEM"})),
    ("gpu_vram", frozenset({"vram", "cuda", "gpu", "nvidia", "cublas"})),
    ("ollama_service", frozenset({"ollama", "model pull", "registry.ollama"})),
    ("parse_format", frozenset({"json", "yaml", "parse", "parser", "invalid syntax", "unexpected token"})),
    ("permission", frozenset({"permission denied", "eacces", "forbidden", "unauthorized"})),
    ("subprocess", frozenset({"returncode", "exit code", "non-zero", "command failed", "stderr"})),
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


def fingerprint_for_error(category: str, error: str) -> str:
    """Stable fingerprint for clustering the same failure mode (category + error text)."""

    key = json.dumps(
        {"c": normalize_text(category), "e": normalize_text(error)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def infer_pattern_tags(category: str, error: str) -> list[str]:
    """Derive coarse pattern tags from category and error text."""

    blob = f"{category} {error}".lower()
    tags: list[str] = []
    for tag, needles in _PATTERN_RULES:
        if any(n in blob for n in needles):
            tags.append(tag)
    if not tags:
        tags.append("general")
    # Stable order, unique
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def root_cause_hint(tags: Sequence[str], error: str) -> str:
    """One-line heuristic hypothesis for operators."""

    tag_set = set(tags)
    if "timeout" in tag_set:
        return "Likely slow or hung dependency; reduce batch size, add retries/backoff, or increase limits."
    if "network" in tag_set:
        return "Connectivity or upstream service issue; verify endpoints, firewalls, and DNS."
    if "disk_space" in tag_set:
        return "Storage pressure; purge caches/artifacts and verify volume quotas."
    if "memory" in tag_set or "gpu_vram" in tag_set:
        return "Resource exhaustion; lower concurrency, model size, or context length."
    if "ollama_service" in tag_set:
        return "Local inference stack; check ollama health, model availability, and restart if needed."
    if "parse_format" in tag_set:
        return "Structured output drift; tighten schemas, validate before parse, or constrain the model."
    if "permission" in tag_set:
        return "Authorization or filesystem permissions; verify tokens, ACLs, and service user."
    if "subprocess" in tag_set:
        return "External command failed; inspect stderr, arguments, and environment in the failing step."
    err_preview = error.strip()[:120]
    return f"Review failure context: {err_preview}" if err_preview else "Review surrounding logs and recent changes."


def actionable_insights_for(tags: Sequence[str], category: str) -> list[str]:
    """Short, operator-facing next steps."""

    steps: list[str] = []
    tag_set = set(tags)
    if "ollama_service" in tag_set:
        steps.append("Run: python -m src.self_improvement.auto_engine check (or fix) from the repo root.")
    if "disk_space" in tag_set:
        steps.append("Run auto_fix cleanup or clear temp prefixes (openclaw_, ollama_) under the system temp dir.")
    if "gpu_vram" in tag_set or "memory" in tag_set:
        steps.append("Lower parallel load; confirm no runaway processes via nvidia-smi / system monitor.")
    if "parse_format" in tag_set:
        steps.append("Add validation gates before parsers; prefer fenced, bounded output from the model.")
    if "timeout" in tag_set:
        steps.append("Increase timeouts only after ruling out stalls; add chunked work and heartbeats.")
    if "network" in tag_set:
        steps.append("Verify outbound connectivity and retry with exponential backoff.")
    if not steps:
        steps.append(f"Search prior learnings: error_learning search --log-path <path> \"{category}\"")
    return steps[:6]


def merge_lesson_text(existing: str, new: str) -> str:
    """Combine lessons without naive duplication."""

    a, b = existing.strip(), new.strip()
    if not b:
        return a
    if not a:
        return b
    if b.lower() in a.lower() or a.lower() in b.lower():
        return a if len(a) >= len(b) else b
    return f"{a.rstrip('.')}. {b}"


def canonical_payload(category: str, error: str, lesson: str, resolved: bool) -> dict[str, object]:
    """Return a normalized payload used for IDs and full-entry deduplication."""

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
    first_seen: str | None = None,
    last_seen: str | None = None,
    sources: list[dict[str, str]] | None = None,
    pattern_tags: list[str] | None = None,
    root_cause_hint_value: str | None = None,
    actionable_insights: list[str] | None = None,
    fingerprint: str | None = None,
) -> dict[str, object]:
    """Create a log entry that matches the JSON schema."""

    payload = canonical_payload(category, error, lesson, resolved)
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    fp = fingerprint or fingerprint_for_error(category, error)
    tags = pattern_tags if pattern_tags is not None else infer_pattern_tags(category, error)
    rca = root_cause_hint_value or root_cause_hint(tags, error)
    insights = actionable_insights if actionable_insights is not None else actionable_insights_for(tags, category)
    created_at = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    fs = first_seen or created_at
    ls = last_seen or created_at
    return {
        "id": digest,
        "fingerprint": fp,
        "timestamp": created_at,
        "first_seen": fs,
        "last_seen": ls,
        "occurrence_count": int(occurrence_count),
        "category": category.strip(),
        "error": error.strip(),
        "lesson": lesson.strip(),
        "resolved": bool(resolved),
        "pattern_tags": tags,
        "root_cause_hint": rca,
        "actionable_insights": insights,
        "sources": list(sources or []),
    }


def default_store() -> dict[str, object]:
    """Return an empty log document."""

    return {"schema_version": SCHEMA_VERSION, "entries": []}


def _coerce_sources(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        ref = item.get("ref")
        if isinstance(t, str) and isinstance(ref, str) and t.strip() and ref.strip():
            out.append({"type": t.strip(), "ref": ref.strip()})
    return out


def _migrate_entry_fields(entry: dict[str, object]) -> dict[str, object]:
    """Ensure v2 fields exist on a validated base entry."""

    category = str(entry["category"])
    error = str(entry["error"])
    lesson = str(entry["lesson"])
    resolved = bool(entry["resolved"])
    ts = str(entry["timestamp"])

    if "fingerprint" not in entry or not str(entry.get("fingerprint", "")).strip():
        entry["fingerprint"] = fingerprint_for_error(category, error)

    tags = entry.get("pattern_tags")
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        entry["pattern_tags"] = infer_pattern_tags(category, error)
    else:
        entry["pattern_tags"] = [str(t) for t in tags]

    if not isinstance(entry.get("root_cause_hint"), str) or not str(entry["root_cause_hint"]).strip():
        entry["root_cause_hint"] = root_cause_hint(list(entry["pattern_tags"]), error)

    ai = entry.get("actionable_insights")
    if not isinstance(ai, list) or not all(isinstance(x, str) for x in ai):
        entry["actionable_insights"] = actionable_insights_for(list(entry["pattern_tags"]), category)
    else:
        entry["actionable_insights"] = [str(x) for x in ai]

    if not isinstance(entry.get("first_seen"), str) or not str(entry["first_seen"]).strip():
        entry["first_seen"] = ts
    if not isinstance(entry.get("last_seen"), str) or not str(entry["last_seen"]).strip():
        entry["last_seen"] = ts

    oc = entry.get("occurrence_count", 1)
    try:
        entry["occurrence_count"] = max(1, int(oc))
    except (TypeError, ValueError):
        entry["occurrence_count"] = 1

    entry["sources"] = _coerce_sources(entry.get("sources"))
    return entry


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
        built = build_entry(
            entry["category"],
            entry["error"],
            entry["lesson"],
            resolved=resolved,
            timestamp=entry["timestamp"],
        )
        entry["id"] = str(built["id"])
        for k in ("fingerprint", "first_seen", "last_seen", "pattern_tags", "root_cause_hint", "actionable_insights"):
            if k not in entry:
                entry[k] = built[k]

    entry["resolved"] = resolved
    return _migrate_entry_fields(entry)


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

    file_schema = int(raw.get("schema_version", 1))
    store = {
        "schema_version": max(SCHEMA_VERSION, file_schema),
        "entries": [validate_entry(item) for item in raw_entries],
    }
    return store


def save_store(log_path: Path, store: dict[str, object]) -> None:
    """Persist the error log to disk (atomic replace)."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    store = dict(store)
    store["schema_version"] = max(int(store.get("schema_version", SCHEMA_VERSION)), SCHEMA_VERSION)
    payload = json.dumps(store, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".error_log_", suffix=".json", dir=str(log_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, log_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def entries_match(left: dict[str, object], right: dict[str, object]) -> bool:
    """Return True when two entries are the same learning (full duplicate)."""

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


def _fingerprint_match(left: dict[str, object], right: dict[str, object]) -> bool:
    return str(left.get("fingerprint", "")) == str(right.get("fingerprint", "")) and str(left["fingerprint"])


def add_entry(
    log_path: Path,
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
    source: dict[str, str] | None = None,
) -> tuple[dict[str, object], str]:
    """Add or merge an error learning entry.

    Returns ``(entry, status)`` where status is one of:
    ``created``, ``duplicate``, ``merged`` (same failure fingerprint, new lesson
    or metadata), ``updated`` (occurrence bump with same lesson).
    """

    store = load_store(log_path)
    entries = store["entries"]
    assert isinstance(entries, list)

    new_entry = build_entry(category, error, lesson, resolved=resolved)
    if source:
        src_list = list(new_entry["sources"])
        assert isinstance(src_list, list)
        if source not in src_list:
            src_list.append(source)
        new_entry["sources"] = src_list

    for i, entry in enumerate(entries):
        validated = validate_entry(entry)
        if entries_match(validated, new_entry):
            return validated, "duplicate"

    now_ts = str(new_entry["last_seen"])
    for i, entry in enumerate(entries):
        validated = validate_entry(entry)
        if _fingerprint_match(validated, new_entry):
            prev_lesson = str(validated["lesson"])
            merged_lesson = merge_lesson_text(prev_lesson, lesson)
            count = int(validated["occurrence_count"]) + 1
            validated["occurrence_count"] = count
            validated["last_seen"] = now_ts
            validated["lesson"] = merged_lesson
            validated["resolved"] = bool(validated["resolved"] or resolved)
            if source:
                sl = list(validated["sources"])
                if source not in sl:
                    sl.append(source)
                validated["sources"] = sl
            # Refresh hints if tags unchanged; re-infer tags if error text grew
            validated["pattern_tags"] = infer_pattern_tags(str(validated["category"]), str(validated["error"]))
            validated["root_cause_hint"] = root_cause_hint(validated["pattern_tags"], str(validated["error"]))
            validated["actionable_insights"] = actionable_insights_for(
                validated["pattern_tags"], str(validated["category"])
            )
            entries[i] = validated
            entries.sort(key=lambda item: str(item["last_seen"]), reverse=True)
            save_store(log_path, store)
            status = "merged" if merged_lesson != prev_lesson else "updated"
            return validated, status

    entries.append(new_entry)
    entries.sort(key=lambda item: str(item["last_seen"]), reverse=True)
    save_store(log_path, store)
    return new_entry, "created"


def format_entry(entry: dict[str, object]) -> str:
    """Render a single entry for console output."""

    category = str(entry["category"])
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    occ = int(entry.get("occurrence_count", 1))
    lines = [
        (
            f"{colorize(category, category_color(category))} "
            f"{colorize(f'[{status_text}]', status_color)} "
            f"{colorize(str(entry['last_seen']), 'cyan')} "
            f"{colorize(f'(×{occ})', 'yellow')}"
        ),
        f"  {colorize('ID:', 'yellow')} {entry['id']}  {colorize('fp:', 'cyan')}{entry.get('fingerprint', '')}",
        f"  {colorize('Error:', 'red')} {entry['error']}",
        f"  {colorize('Lesson:', 'green')} {entry['lesson']}",
        f"  {colorize('RCA:', 'cyan')} {entry.get('root_cause_hint', '')}",
    ]
    tags = entry.get("pattern_tags")
    if isinstance(tags, list) and tags:
        lines.append(f"  {colorize('Patterns:', 'yellow')} {', '.join(str(t) for t in tags)}")
    insights = entry.get("actionable_insights")
    if isinstance(insights, list) and insights:
        lines.append(f"  {colorize('Next steps:', 'green')}")
        for step in insights[:5]:
            lines.append(f"    - {step}")
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
    weighted = sum(int(e.get("occurrence_count", 1)) for e in entries)
    print(colorize(f"Unique entries: {total}  |  Total occurrences: {weighted}", "cyan"))
    for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (count / total) * 100
        print(
            f"- {colorize(category, category_color(category))}: "
            f"{colorize(str(count), 'red')} "
            f"({share:.1f}%)"
        )


@dataclass(frozen=True)
class ErrorPattern:
    """Cluster of learnings sharing the same failure fingerprint."""

    fingerprint: str
    occurrence_total: int
    categories: tuple[str, ...]
    pattern_tags: tuple[str, ...]
    sample_error: str
    representative: dict[str, object]
    entry_ids: tuple[str, ...]


def detect_patterns(entries: list[dict[str, object]], *, min_occurrences: int = 1) -> list[ErrorPattern]:
    """Group entries by fingerprint and rank by total occurrences."""

    clusters: dict[str, list[dict[str, object]]] = defaultdict(list)
    for entry in entries:
        ve = validate_entry(entry)
        fp = str(ve["fingerprint"])
        clusters[fp].append(ve)

    patterns: list[ErrorPattern] = []
    for fp, group in clusters.items():
        occ_total = sum(int(e.get("occurrence_count", 1)) for e in group)
        if occ_total < min_occurrences:
            continue
        group.sort(key=lambda e: (-int(e.get("occurrence_count", 1)), str(e["last_seen"])), reverse=False)
        rep = group[0]
        cats = tuple(sorted({str(e["category"]) for e in group}))
        tags = tuple(str(t) for t in rep.get("pattern_tags", []) if isinstance(rep.get("pattern_tags"), list))
        if not tags:
            tags = tuple(infer_pattern_tags(str(rep["category"]), str(rep["error"])))
        patterns.append(
            ErrorPattern(
                fingerprint=fp,
                occurrence_total=occ_total,
                categories=cats,
                pattern_tags=tags,
                sample_error=str(rep["error"]),
                representative=rep,
                entry_ids=tuple(str(e["id"]) for e in group),
            )
        )
    patterns.sort(key=lambda p: (-p.occurrence_total, p.fingerprint))
    return patterns


def print_patterns(entries: list[dict[str, object]], *, min_occurrences: int = 1) -> None:
    """Print detected error patterns (clusters)."""

    print(colorize("Error Patterns (by fingerprint)", "bold"))
    print(colorize("================================", "cyan"))
    patterns = detect_patterns(entries, min_occurrences=min_occurrences)
    if not patterns:
        print(colorize("No patterns at or above the occurrence threshold.", "yellow"))
        return
    for i, p in enumerate(patterns):
        if i:
            print()
        head = (
            f"{colorize(f'×{p.occurrence_total}', 'red')}  "
            f"{colorize(p.fingerprint, 'cyan')}  "
            f"{', '.join(colorize(c, category_color(c)) for c in p.categories)}"
        )
        print(head)
        print(f"  {colorize('Tags:', 'yellow')} {', '.join(p.pattern_tags)}")
        print(f"  {colorize('Sample:', 'red')} {p.sample_error[:200]}{'…' if len(p.sample_error) > 200 else ''}")


def print_insights(entries: list[dict[str, object]], *, limit: int = 8) -> None:
    """Print prioritized actionable insights (open issues and hot patterns first)."""

    print(colorize("Actionable Insights", "bold"))
    print(colorize("===================", "cyan"))
    validated = [validate_entry(e) for e in entries]
    if not validated:
        print(colorize("No entries yet. Ingest logs or add failures manually.", "yellow"))
        return

    patterns = detect_patterns(validated, min_occurrences=1)
    open_entries = [e for e in validated if not bool(e["resolved"])]

    def score_entry(e: dict[str, object]) -> tuple[int, int, str]:
        hot = 0 if bool(e["resolved"]) else 1
        return (hot, int(e.get("occurrence_count", 1)), str(e["last_seen"]))

    ranked = sorted(validated, key=score_entry, reverse=True)

    shown = 0
    print(colorize("Top issues", "bold"))
    for e in ranked:
        if shown >= limit:
            break
        print()
        print(format_entry(e))
        shown += 1

    hot_patterns = [p for p in patterns if p.occurrence_total >= 2]
    if hot_patterns:
        print()
        print(colorize("Recurring patterns (≥2 occurrences)", "bold"))
        for p in hot_patterns[:limit]:
            rep = p.representative
            print()
            print(
                f"- {colorize(str(p.occurrence_total) + '×', 'red')} "
                f"{colorize(p.fingerprint, 'cyan')} "
                f"{' / '.join(p.categories)}"
            )
            print(f"  {rep.get('root_cause_hint', '')}")
            ins = rep.get("actionable_insights")
            if isinstance(ins, list):
                for step in ins[:3]:
                    print(f"    • {step}")


def search_score(query: str, entry: dict[str, object]) -> float:
    """Score how relevant an entry is to a search query."""

    normalized_query = normalize_text(query)
    haystack = normalize_text(
        " ".join(
            (
                str(entry["category"]),
                str(entry["error"]),
                str(entry["lesson"]),
                str(entry.get("root_cause_hint", "")),
                " ".join(str(t) for t in entry.get("pattern_tags", []) if isinstance(entry.get("pattern_tags"), list)),
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
        ve = validate_entry(entry)
        score = search_score(query, ve)
        if score >= 0.45:
            ranked.append((score, ve))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["last_seen"])), reverse=False)
    return [entry for _, entry in ranked[:limit]]


def _detail_text(details: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("stderr", "stdout", "message", "returncode"):
        if key in details and details[key]:
            parts.append(f"{key}={details[key]}")
    if not parts:
        try:
            return json.dumps(dict(details), sort_keys=True)[:500]
        except (TypeError, ValueError):
            return str(details)[:500]
    return "; ".join(str(p) for p in parts)


def improvement_entry_to_learning(entry: Mapping[str, Any], *, ref: str) -> tuple[str, str, str, bool] | None:
    """Map one auto-improvement log dict to (category, error, lesson, resolved) or None if skipped."""

    category = str(entry.get("category", "auto_improvement"))
    action = str(entry.get("action", ""))
    outcome = str(entry.get("outcome", ""))
    details = entry.get("details") if isinstance(entry.get("details"), dict) else {}

    resolved = outcome in {"restarted", "cleared", "logged", "no_action_needed", "ok"}
    if outcome == "failed":
        err = f"{action} failed: {_detail_text(details)}"
        tags = infer_pattern_tags(category, err)
        lesson = (
            f"Ingested from self-improvement log ({ref}). "
            f"Suggested focus: {', '.join(tags)}. "
            f"{root_cause_hint(tags, err)}"
        )
        return category, err[:4000], lesson[:4000], False

    if category == "warning" and isinstance(details, dict):
        name = str(details.get("name", ""))
        status = str(details.get("status", ""))
        if status in {"warning", "critical"}:
            msg = str(details.get("message", action))
            err = f"{name or 'check'} health [{status}]: {msg}"
            tags = infer_pattern_tags(category, err)
            lesson = (
                f"Auto-captured health signal from self-improvement ({ref}). "
                f"{root_cause_hint(tags, err)}"
            )
            return "health_check", err[:4000], lesson[:4000], False

    return None


def iter_auto_improvement_json_files(log_dir: Path) -> Iterator[Path]:
    """Yield sorted daily JSON logs produced by ``AutoImprovementEngine``."""

    if not log_dir.is_dir():
        return
    yield from sorted(log_dir.glob("auto_improvements_*.json"))


def iter_auto_improvement_entries(log_dir: Path, *, since_days: int | None = None) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(file_stem, entry_dict)`` from improvement logs."""

    cutoff: datetime | None = None
    if since_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)

    for path in iter_auto_improvement_json_files(log_dir):
        if cutoff is not None:
            try:
                stem = path.stem.rsplit("_", 1)[-1]
                file_day = datetime.strptime(stem, "%Y%m%d").replace(tzinfo=timezone.utc)
                if file_day < cutoff:
                    continue
            except ValueError:
                pass
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, list):
            continue
        ref = path.name
        for item in payload:
            if isinstance(item, dict):
                yield ref, item


def sync_from_auto_improvement(
    log_path: Path,
    improvement_log_dir: Path,
    *,
    since_days: int | None = 30,
) -> dict[str, int]:
    """Ingest failures and health warnings from auto-improvement JSON into the error log.

    Returns counts: ``processed``, ``learned``, ``skipped``, ``duplicate``,
    ``merged``, ``updated``.
    """

    counts = {"processed": 0, "learned": 0, "skipped": 0, "duplicate": 0, "merged": 0, "updated": 0}
    for ref, raw in iter_auto_improvement_entries(improvement_log_dir, since_days=since_days):
        counts["processed"] += 1
        mapped = improvement_entry_to_learning(raw, ref=ref)
        if not mapped:
            counts["skipped"] += 1
            continue
        cat, err, lesson, resolved = mapped
        _entry, status = add_entry(
            log_path,
            cat,
            err,
            lesson,
            resolved=resolved,
            source={"type": "auto_improvement", "ref": ref},
        )
        if status == "created":
            counts["learned"] += 1
        elif status == "duplicate":
            counts["duplicate"] += 1
        elif status == "merged":
            counts["merged"] += 1
        elif status == "updated":
            counts["updated"] += 1
    return counts


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Capture, cluster, and learn from OpenClaw errors; integrate with self-improvement logs."
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
    patterns_parser = subparsers.add_parser(
        "patterns",
        help="Show recurring error clusters (fingerprints); use --json for machine-readable output.",
    )
    patterns_parser.add_argument(
        "--min-occurrences",
        type=int,
        default=1,
        help="Minimum total occurrences to include a cluster.",
    )
    patterns_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit pattern clusters as JSON instead of text.",
    )

    search_parser = subparsers.add_parser("search", help="Search for relevant past errors.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of matching entries to print.",
    )

    insights_parser = subparsers.add_parser("insights", help="Show prioritized actionable insights.")
    insights_parser.add_argument(
        "--limit",
        type=int,
        default=8,
        help="Maximum number of top issues to print.",
    )

    sync_parser = subparsers.add_parser(
        "sync",
        help="Ingest failures from AutoImprovementEngine JSON logs into this error log.",
    )
    sync_parser.add_argument(
        "--improvement-log-dir",
        type=Path,
        default=DEFAULT_IMPROVEMENT_LOG_DIR,
        help="Directory containing auto_improvements_YYYYMMDD.json (default: <repo>/logs).",
    )
    sync_parser.add_argument(
        "--since-days",
        type=int,
        default=30,
        help="Only read improvement log files on or after this rolling window (default: 30).",
    )
    sync_parser.add_argument(
        "--all-time",
        action="store_true",
        help="Ignore --since-days and scan all improvement logs in the directory.",
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
            entry, status = add_entry(
                args.log_path,
                args.error_category,
                args.error_message,
                args.lesson_learned,
                resolved=args.resolved,
                source={"type": "cli_add", "ref": "error_learning add"},
            )
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1

        if status == "created":
            print(colorize("Saved error learning entry.", "green"))
        elif status == "duplicate":
            print(colorize("Duplicate entry detected; existing learning kept.", "yellow"))
        elif status == "merged":
            print(colorize("Merged with existing failure pattern (lesson or sources updated).", "cyan"))
        elif status == "updated":
            print(colorize("Updated existing pattern (occurrence count).", "cyan"))
        print(format_entry(entry))
        return 0

    validated_entries = [validate_entry(entry) for entry in entries]

    if args.command == "list":
        print_entries(validated_entries, heading="OpenClaw Error Learnings")
        return 0

    if args.command == "stats":
        print_stats(validated_entries)
        return 0

    if args.command == "patterns":
        min_occ = max(1, args.min_occurrences)
        if args.json:
            patterns = detect_patterns(validated_entries, min_occurrences=min_occ)
            payload = [
                {
                    "fingerprint": p.fingerprint,
                    "occurrence_total": p.occurrence_total,
                    "categories": list(p.categories),
                    "pattern_tags": list(p.pattern_tags),
                    "sample_error": p.sample_error,
                    "entry_ids": list(p.entry_ids),
                }
                for p in patterns
            ]
            print(json.dumps(payload, indent=2))
        else:
            print_patterns(validated_entries, min_occurrences=min_occ)
        return 0

    if args.command == "insights":
        print_insights(validated_entries, limit=max(1, args.limit))
        return 0

    if args.command == "search":
        matches = search_entries(validated_entries, args.query, limit=max(args.limit, 1))
        print_entries(matches, heading=f"Search Results: {args.query}")
        return 0

    if args.command == "sync":
        since = None if args.all_time else max(0, args.since_days)
        try:
            summary = sync_from_auto_improvement(
                args.log_path,
                args.improvement_log_dir,
                since_days=since,
            )
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1
        print(colorize("Sync complete (self-improvement → error log)", "green"))
        for k, v in summary.items():
            print(f"  {k}: {v}")
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
