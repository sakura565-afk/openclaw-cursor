#!/usr/bin/env python3
"""Capture and learn from recurring OpenClaw session errors.

Supports manual CLI entries (JSON log), automatic ingestion from agent logs into
`.learnings/error_log.md`, and optional sync into the JSON store. Re-runs are
idempotent: duplicate incidents reuse the same stable entry id and are skipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterator, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = ROOT / ".learnings" / "error_log.json"
SCAN_STATE_NAME = ".error_learning_scan_state.json"
MAX_LOG_FILE_BYTES = 2 * 1024 * 1024
SCAN_OVERLAP_MINUTES = 90
SCHEMA_VERSION = 1
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
    source: str | None = None,
) -> dict[str, object]:
    """Create a log entry that matches the JSON schema."""

    payload = canonical_payload(category, error, lesson, resolved)
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    created_at = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    out: dict[str, object] = {
        "id": digest,
        "timestamp": created_at,
        "category": category.strip(),
        "error": error.strip(),
        "lesson": lesson.strip(),
        "resolved": bool(resolved),
    }
    if source and str(source).strip():
        out["source"] = str(source).strip()
    return out


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
            source=str(entry["source"]) if isinstance(entry.get("source"), str) else None,
        )["id"]
    entry["resolved"] = resolved
    src = entry.get("source")
    if src is not None:
        if not isinstance(src, str):
            raise ErrorLearningError("Entry field 'source' must be a string when present.")
        if src.strip():
            entry["source"] = src.strip()
        else:
            entry.pop("source", None)
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


# --- Error classification and log scanning ---------------------------------

FAILURE_LINE = re.compile(
    r"(?i)(\b(traceback|exception:|assertionerror|assertion failed|"
    r"error:|failed:|failure:|\[?\s*error\s*\]?|fatal:|critical:)\b|"
    r"\bHTTP\s+[45]\d\d\b|exit\s*code\s*[1-9]\d*\b)"
)
TRACEBACK_START = re.compile(r"(?i)^traceback\s*\(most recent call last\)\s*:\s*$")
EXCEPTION_TAIL = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*):\s*(.+)$")

CLASSIFICATION_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b429\b|rate[-\s]?limit|too many requests|throttl"), "rate_limit"),
    (re.compile(r"(?i)\b401\b|unauthorized|invalid\s+(api\s+)?key|bad\s+credentials|not\s+authenticated"), "auth"),
    (re.compile(r"(?i)\b403\b|forbidden|access\s+denied|permission\s+denied\s+to\s+resource"), "auth"),
    (re.compile(r"(?i)\bmcp\b|\btool\s+(call|execution)\b|function\s+tool|plugin\s+error"), "tool"),
    (re.compile(r"(?i)jsondecode|yaml\.|parse\s+error|unexpected\s+token|invalid\s+json"), "parser"),
    (re.compile(r"(?i)\btimeout\b|timed\s*out|deadline\s+exceeded|read\s+timeout|connect\s+timeout"), "timeout"),
    (re.compile(r"(?i)\b5\d\d\b|bad\s+gateway|service\s+unavailable|connection\s+refused|econnrefused"), "api"),
    (re.compile(r"(?i)\b404\b|not\s+found|no\s+such\s+endpoint"), "api"),
    (re.compile(r"(?i)urllib|httperror|requests\.exceptions|aiohttp|httpx"), "api"),
    (re.compile(r"(?i)enotfound|econnreset|network\s+unreachable|dns|getaddrinfo"), "network"),
    (re.compile(r"(?i)permission\s+denied|eacces|operation\s+not\s+permitted"), "permission"),
    (re.compile(r"(?i)\bgit\b.*(merge|conflict|error)|detached\s+head"), "git"),
    (re.compile(r"(?i)pytest|unittest\.|tests?\s+failed"), "testing"),
)


def classify_error_type(message: str) -> str:
    """Assign a coarse error family for agent and integration logs."""

    for pattern, label in CLASSIFICATION_RULES:
        if pattern.search(message):
            return label
    if re.search(
        r"(?i)(type|value|key|index|attribute|runtime|recursion|notimplemented|"
        r"stopiteration|zerodivision|assertion)error|"
        r"exception\b",
        message,
    ):
        return "runtime"
    return "general"


def suggest_lesson(category: str) -> str:
    """Short default remediation hint keyed by classified category."""

    hints = {
        "rate_limit": "Reduce request rate, add exponential backoff, and cache responses where safe.",
        "auth": "Rotate or export credentials, confirm scopes, and avoid logging secrets.",
        "tool": "Validate tool inputs and outputs; retry once with a narrower tool call.",
        "parser": "Tighten output contracts (fenced JSON/YAML), validate before parsing, and truncate huge payloads.",
        "timeout": "Increase client timeouts, split work into smaller steps, or move to async retries.",
        "api": "Inspect HTTP status and response body; verify base URL, headers, and upstream health.",
        "network": "Check DNS, VPN, firewall rules, and transient connectivity before retrying.",
        "permission": "Run with sufficient filesystem or OS permissions, or adjust paths and umask.",
        "git": "Resolve conflicts locally, ensure the branch exists, and verify remotes before pushing.",
        "testing": "Re-run the failing test in isolation and capture the full assertion diff.",
        "runtime": "Inspect the stack trace, add guards for None/empty values, and reproduce with minimal input.",
        "general": "Capture surrounding log lines and reproduce with a minimal failing command.",
    }
    return hints.get(category, hints["general"])


def md_path_for_json_log(json_log_path: Path) -> Path:
    """Markdown log colocated with the JSON store (typically under `.learnings/`)."""

    return json_log_path.parent / "error_log.md"


def _scan_state_path(root: Path) -> Path:
    return root / ".learnings" / SCAN_STATE_NAME


def load_scan_state(root: Path) -> dict[str, Any]:
    path = _scan_state_path(root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_scan_state(root: Path, data: dict[str, Any]) -> None:
    path = _scan_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iter_scan_files(root: Path, globs: tuple[str, ...], cutoff: datetime) -> list[Path]:
    """Collect recent session files under root (same shape as auto_reflection)."""

    seen: set[Path] = set()
    out: list[Path] = []
    for pattern in globs:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if datetime.fromtimestamp(st.st_mtime, tz=timezone.utc) < cutoff:
                continue
            if st.st_size > MAX_LOG_FILE_BYTES:
                continue
            rp = path.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            out.append(path)
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def _consume_traceback(lines: list[str], start: int) -> tuple[str, int]:
    """Return traceback text and index after the exception line."""

    buf: list[str] = []
    i = start
    while i < len(lines):
        buf.append(lines[i])
        if i > start and EXCEPTION_TAIL.match(lines[i].strip()):
            return "\n".join(buf).strip(), i + 1
        i += 1
    return "\n".join(buf).strip(), i


def extract_error_snippets_from_text(rel_path: str, raw: str) -> Iterator[tuple[str, str]]:
    """Yield (source_ref, error_snippet) from plain text logs."""

    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if TRACEBACK_START.match(stripped):
            block, j = _consume_traceback(lines, i)
            if len(block) > 40:
                yield (f"`{rel_path}` (traceback)", block[:8000])
            i = j
            continue
        if FAILURE_LINE.search(line) and len(stripped) > 12:
            yield (f"`{rel_path}` (line {i + 1})", stripped[:4000])
        i += 1


def _walk_json_for_errors(obj: Any, rel_path: str, sink: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and re.search(r"(?i)\b(error|stderr|message|detail|reason)\b", k):
                if isinstance(v, str) and FAILURE_LINE.search(v) and len(v.strip()) > 8:
                    sink.append(v.strip())
            _walk_json_for_errors(v, rel_path, sink)
    elif isinstance(obj, list):
        for item in obj:
            _walk_json_for_errors(item, rel_path, sink)


def extract_error_snippets_from_json(rel_path: str, raw: str) -> Iterator[tuple[str, str]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        yield from extract_error_snippets_from_text(rel_path, raw)
        return
    found: list[str] = []
    _walk_json_for_errors(data, rel_path, found)
    for idx, msg in enumerate(found):
        yield (f"`{rel_path}` (json #{idx + 1})", msg[:8000])


def read_file_snippets(path: Path, root: Path) -> Iterator[tuple[str, str]]:
    rel = path.relative_to(root).as_posix()
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if path.suffix.lower() == ".json":
        yield from extract_error_snippets_from_json(rel, raw)
    else:
        yield from extract_error_snippets_from_text(rel, raw)


def parse_error_log_md(md_path: Path) -> list[dict[str, Any]]:
    """Parse entries written by `render_error_log_md` (idempotent round-trip)."""

    if not md_path.exists():
        return []
    text = md_path.read_text(encoding="utf-8")
    parts = re.split(r"(?m)^## id:([a-f0-9]{12})\s*$", text)
    if len(parts) < 3:
        return []
    entries: list[dict[str, Any]] = []
    # parts[0] = preamble; then pairs (id, body), (id, body), ...
    for idx in range(1, len(parts), 2):
        entry_id = parts[idx]
        body = parts[idx + 1] if idx + 1 < len(parts) else ""
        m_class = re.search(r"(?m)^- \*\*Class\*\*:\s*(.+)\s*$", body)
        m_ts = re.search(r"(?m)^- \*\*Timestamp\*\*:\s*(.+)\s*$", body)
        m_src = re.search(r"(?m)^- \*\*Source\*\*:\s*(.+)\s*$", body)
        m_res = re.search(r"(?m)^- \*\*Resolved\*\*:\s*(.+)\s*$", body)
        err_m = re.search(
            r"\*\*Error\*\*\s*\n+```(?:[^\n`]*)\n(.*?)```",
            body,
            flags=re.DOTALL,
        )
        les_m = re.search(
            r"\*\*Lesson\*\*\s*\n+```(?:[^\n`]*)\n(.*?)```",
            body,
            flags=re.DOTALL,
        )
        if not (m_class and m_ts and err_m and les_m):
            continue
        resolved_s = (m_res.group(1).strip().lower() if m_res else "yes")
        resolved = resolved_s in ("yes", "true", "1", "resolved")
        entry: dict[str, Any] = {
            "id": entry_id,
            "timestamp": m_ts.group(1).strip(),
            "category": m_class.group(1).strip(),
            "error": err_m.group(1).strip(),
            "lesson": les_m.group(1).strip(),
            "resolved": resolved,
        }
        if m_src:
            inner = m_src.group(1).strip().strip("`")
            if inner and inner != "_unknown_":
                entry["source"] = inner
        try:
            entries.append(validate_entry(entry))
        except ErrorLearningError:
            continue
    return entries


def render_error_log_md(entries: Sequence[dict[str, Any]], *, updated_at: str) -> str:
    """Structured markdown aligned with other `.learnings/` artifacts (headings, fenced bodies)."""

    lines = [
        "# Agent error learning log",
        "",
        "<!-- error-learning-md v1 | generated by scripts/error_learning.py -->",
        "",
        f"_Last updated (UTC): {updated_at}_",
        "",
        "## Summary",
        "",
    ]
    if not entries:
        lines.extend(["- **Total entries**: 0", "- **Open incidents**: 0", "", "_No entries yet._", ""])
        return "\n".join(lines)

    validated = [validate_entry(dict(e)) for e in entries]
    open_n = sum(1 for e in validated if not e["resolved"])
    by_cat = Counter(str(e["category"]) for e in validated)
    lines.append(f"- **Total entries**: {len(validated)}")
    lines.append(f"- **Open incidents**: {open_n}")
    lines.append("")
    lines.append("| Class | Count |")
    lines.append("| --- | ---: |")
    for cat, n in sorted(by_cat.items(), key=lambda x: (-x[1], x[0].lower())):
        lines.append(f"| {cat} | {n} |")
    lines.append("")
    lines.append("## Entries")
    lines.append("")

    for e in sorted(validated, key=lambda x: str(x["timestamp"]), reverse=True):
        eid = str(e["id"])
        src = e.get("source")
        src_line = f"- **Source**: `{src}`" if isinstance(src, str) and src.strip() else "- **Source**: _unknown_"
        res_word = "yes" if e["resolved"] else "no"
        lines.append(f"## id:{eid}")
        lines.append("")
        lines.append(f"- **Class**: {e['category']}")
        lines.append(f"- **Timestamp**: {e['timestamp']}")
        lines.append(src_line)
        lines.append(f"- **Resolved**: {res_word}")
        lines.append("")
        lines.append("**Error**")
        lines.append("")
        lines.append("```text")
        lines.append(str(e["error"]).strip())
        lines.append("```")
        lines.append("")
        lines.append("**Lesson**")
        lines.append("")
        lines.append("```text")
        lines.append(str(e["lesson"]).strip())
        lines.append("```")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def merge_entries_for_md_and_disk(
    md_path: Path,
    incoming: list[dict[str, object]],
    *,
    json_path: Path | None,
) -> tuple[int, int]:
    """Merge JSON store, existing markdown, and new candidates; returns (new_incidents_merged, total_entries)."""

    json_entries: list[dict[str, object]] = []
    if json_path is not None:
        st = load_store(json_path)
        raw = st.get("entries", [])
        if isinstance(raw, list):
            json_entries = [validate_entry(dict(x)) for x in raw]

    md_entries = parse_error_log_md(md_path)

    pool: list[dict[str, object]] = []

    def merge_one(entry: dict[str, object]) -> bool:
        """Return True if this entry was newly added to the pool."""

        v = validate_entry(dict(entry))
        for i, existing in enumerate(pool):
            if entries_match(v, existing):
                if (not existing.get("source")) and v.get("source"):
                    ex = dict(existing)
                    ex["source"] = v["source"]
                    pool[i] = validate_entry(ex)
                return False
        pool.append(v)
        return True

    for e in json_entries:
        merge_one(e)
    for e in md_entries:
        merge_one(e)

    added = 0
    for e in incoming:
        if merge_one(e):
            added += 1

    pool.sort(key=lambda x: str(x["timestamp"]), reverse=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_error_log_md(pool, updated_at=now), encoding="utf-8")

    if json_path is not None:
        save_store(json_path, {"schema_version": SCHEMA_VERSION, "entries": pool})

    return added, len(pool)


def run_scan(
    root: Path,
    *,
    since_hours: float,
    extra_globs: list[str],
    md_path: Path,
    json_path: Path | None,
    overlap_minutes: int = SCAN_OVERLAP_MINUTES,
) -> tuple[int, int, int, list[str]]:
    """Scan logs since last run (or since_hours), classify, and merge into markdown."""

    from scripts.auto_reflection import collect_globs, utc_now

    started = utc_now()
    state = load_scan_state(root)
    raw_last = state.get("last_scan_utc")
    if raw_last:
        try:
            last_dt = datetime.fromisoformat(raw_last.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            cutoff = last_dt - timedelta(minutes=overlap_minutes)
        except ValueError:
            cutoff = started - timedelta(hours=since_hours)
    else:
        cutoff = started - timedelta(hours=since_hours)

    globs = collect_globs(extra_globs)
    files = iter_scan_files(root, globs, cutoff)
    candidates: list[dict[str, object]] = []
    for path in files:
        for source_ref, snippet in read_file_snippets(path, root):
            cat = classify_error_type(snippet)
            lesson = suggest_lesson(cat)
            candidates.append(
                build_entry(
                    cat,
                    snippet,
                    lesson,
                    resolved=False,
                    source=source_ref,
                )
            )

    md_added, total = merge_entries_for_md_and_disk(md_path, candidates, json_path=json_path)
    state["last_scan_utc"] = started.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state["last_files_scanned"] = len(files)
    save_scan_state(root, state)
    rel_files = [p.relative_to(root).as_posix() for p in files[:30]]
    return len(files), md_added, total, rel_files


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
) -> tuple[dict[str, object], bool]:
    """Add an error learning entry unless it already exists."""

    store = load_store(log_path)
    new_entry = build_entry(category, error, lesson, resolved=resolved)
    entries = store["entries"]
    assert isinstance(entries, list)
    for entry in entries:
        validated = validate_entry(entry)
        if entries_match(validated, new_entry):
            merge_entries_for_md_and_disk(md_path_for_json_log(log_path), [], json_path=log_path)
            return validated, False

    entries.append(new_entry)
    entries.sort(key=lambda item: str(item["timestamp"]), reverse=True)
    save_store(log_path, store)
    merge_entries_for_md_and_disk(md_path_for_json_log(log_path), [], json_path=log_path)
    return new_entry, True


def format_entry(entry: dict[str, object]) -> str:
    """Render a single entry for console output."""

    category = str(entry["category"])
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    lines = [
        (
            f"{colorize(category, category_color(category))} "
            f"{colorize(f'[{status_text}]', status_color)} "
            f"{colorize(str(entry['timestamp']), 'cyan')}"
        ),
        f"  {colorize('ID:', 'yellow')} {entry['id']}",
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

    counts = Counter(str(entry["category"]) for entry in entries)
    total = len(entries)
    for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())):
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
        " ".join((str(entry["category"]), str(entry["error"]), str(entry["lesson"])))
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

    scan_parser = subparsers.add_parser(
        "scan",
        help="Scan recent agent logs, classify errors, and merge into `.learnings/error_log.md`.",
    )
    scan_parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Workspace root (session files are matched relative to this directory).",
    )
    scan_parser.add_argument(
        "--since-hours",
        type=float,
        default=float(7 * 24),
        help="Lookback window in hours when no prior scan state exists (default: 168).",
    )
    scan_parser.add_argument(
        "--glob",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Extra glob relative to --root (may be repeated). Same semantics as auto_reflection.",
    )
    scan_parser.add_argument(
        "--md-path",
        type=Path,
        default=None,
        help="Markdown output path (default: <root>/.learnings/error_log.md).",
    )
    scan_parser.add_argument(
        "--json-path",
        type=Path,
        default=None,
        help="JSON log path (default: <root>/.learnings/error_log.json).",
    )
    scan_parser.add_argument(
        "--no-json",
        action="store_true",
        help="Update only the markdown log, not the JSON store.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the error learning CLI."""

    args = parse_args(argv)

    if args.command == "scan":
        root = args.root.resolve()
        md_path = args.md_path if args.md_path is not None else root / ".learnings" / "error_log.md"
        json_path: Path | None = None
        if not args.no_json:
            json_path = args.json_path if args.json_path is not None else root / ".learnings" / "error_log.json"
        try:
            n_files, added, total, sample = run_scan(
                root,
                since_hours=args.since_hours,
                extra_globs=list(args.glob),
                md_path=md_path,
                json_path=json_path,
            )
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1
        except Exception as exc:
            print(colorize(f"Scan failed: {exc}", "red"), file=sys.stderr)
            return 1

        print(colorize("Error learning scan", "bold"))
        print(colorize("=" * 22, "cyan"))
        print(f"Markdown log: {md_path}")
        if json_path is not None:
            print(f"JSON log: {json_path}")
        else:
            print("JSON log: (disabled)")
        print(colorize(f"Session files scanned: {n_files}", "bold"))
        print(colorize(f"New unique incidents merged: {added}", "green" if added else "yellow"))
        print(colorize(f"Total deduplicated learnings: {total}", "cyan"))
        if sample:
            print(colorize("Recent files (sample):", "bold"))
            for rel in sample[:12]:
                print(f"  - {rel}")
        return 0

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
        print_entries(validated_entries, heading="OpenClaw Error Learnings")
        return 0

    if args.command == "stats":
        print_stats(validated_entries)
        return 0

    if args.command == "search":
        matches = search_entries(validated_entries, args.query, limit=max(args.limit, 1))
        print_entries(matches, heading=f"Search Results: {args.query}")
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
