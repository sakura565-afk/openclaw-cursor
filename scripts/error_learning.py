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
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = ROOT / ".learnings" / "error_log.json"
SCHEMA_VERSION = 1
STRUCTURED_SCHEMA_VERSION = 1
EVENTS_SUBDIR = "events"

ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}
FALLBACK_CATEGORY_COLORS = ("red", "yellow", "green")

# Ordered rules: (error_type, severity, root_cause_template, regex)
_ERROR_TYPE_RULES: tuple[tuple[str, str, str, re.Pattern[str]], ...] = (
    (
        "rate_limit",
        "high",
        "HTTP 429 or explicit rate limiting from an upstream API",
        re.compile(
            r"(?:\b429\b|rate\s*limit|too\s+many\s+requests|throttl|quota\s+exceeded|resource_exhausted)",
            re.I,
        ),
    ),
    (
        "auth",
        "high",
        "Authentication or authorization was rejected",
        re.compile(
            r"(?:\b401\b|\b403\b|unauthorized|forbidden|invalid\s+api\s*key|bad\s+credentials|"
            r"oauth|access\s+denied|not\s+authenticated)",
            re.I,
        ),
    ),
    (
        "context_limit",
        "high",
        "Prompt or session exceeded model context / token window",
        re.compile(
            r"(?:context\s*(?:length|window|limit)|token\s*limit|maximum\s+context|"
            r"context\s+overflow|too\s+many\s+tokens|input\s+is\s+too\s+long|"
            r"maximum\s+token|exceeds?\s+the\s+context)",
            re.I,
        ),
    ),
    (
        "tool_failure",
        "medium",
        "A tool, MCP, or function invocation failed",
        re.compile(
            r"(?:tool[_\s-]?(?:call|use|failure|error)|function[_\s-]?call|mcp[_\s-]?error|"
            r"invoke\s+failed|tool\s+result\s+error|execution\s+failed)",
            re.I,
        ),
    ),
    (
        "timeout",
        "medium",
        "An operation timed out waiting for a response",
        re.compile(r"(?:\btimeout\b|timed\s+out|deadline\s+exceeded|ETIMEDOUT)", re.I),
    ),
    (
        "network",
        "medium",
        "Network connectivity or transport failure",
        re.compile(
            r"(?:ECONNRESET|ECONNREFUSED|ENOTFOUND|connection\s+refused|connection\s+reset|"
            r"network\s+unreachable|socket\s+error|TLS\s+error|ssl\s+error|dns\s+error)",
            re.I,
        ),
    ),
    (
        "permission",
        "medium",
        "Filesystem or OS permission denied",
        re.compile(r"(?:permission\s+denied|EACCES|EPERM|operation\s+not\s+permitted)", re.I),
    ),
    (
        "parse_error",
        "low",
        "Structured output or serialization could not be parsed",
        re.compile(
            r"(?:json\.decode|JSONDecodeError|yaml\.error|parse\s+error|invalid\s+json|"
            r"unexpected\s+token|unterminated\s+string)",
            re.I,
        ),
    ),
)

_RECOMMENDED_FIXES: dict[str, str] = {
    "rate_limit": "Backoff with exponential delay, reduce request concurrency, and cache stable reads.",
    "auth": "Rotate or verify API keys and tokens; confirm scopes and clock skew.",
    "context_limit": "Trim history, summarize long inputs, or switch to a higher-context model.",
    "tool_failure": "Validate tool arguments against the schema; add retries for idempotent tools.",
    "timeout": "Increase client timeouts for slow tools; split work into smaller steps.",
    "network": "Retry with jitter; verify DNS, proxies, and firewall rules.",
    "permission": "Fix file ownership and modes; run with least privilege where possible.",
    "parse_error": "Constrain model output format (JSON mode / schemas) and validate before parsing.",
    "quota": "Raise limits or reduce usage; batch operations where the provider allows.",
    "unknown": "Capture the full traceback, reproduce in isolation, and add a targeted rule if recurring.",
}

# Lines that suggest an error worth recording (OpenClaw / agent session logs)
_ERROR_LINE_HINT = re.compile(
    r"(?:^|\s)(?:ERROR|Error|CRITICAL|FATAL|Traceback|Exception:|Failed:|failure:)",
    re.I | re.M,
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


def resolve_errors_dir(log_path: Path, explicit: Path | None) -> Path:
    """Directory for structured JSON events and summary.md (alongside legacy log)."""

    if explicit is not None:
        return explicit
    return log_path.parent / "errors"


def categorize_error(text: str) -> tuple[str, str, str]:
    """Return (error_type, severity, root_cause) from free-form log or message text."""

    blob = text if len(text) <= 16000 else text[:16000]
    for err_type, severity, cause, pattern in _ERROR_TYPE_RULES:
        if pattern.search(blob):
            return err_type, severity, cause
    if re.search(r"\b(?:quota|billing|payment\s+required|402)\b", blob, re.I):
        return "quota", "high", "Account quota or billing limit was hit"
    return "unknown", "low", "No specific pattern matched; review the raw message"


def structured_fingerprint(error_type: str, message: str) -> str:
    """Stable id for deduplication of structured events."""

    normalized = normalize_text(message)[:4000]
    raw = f"{error_type}|{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def colorize_category(category: str) -> str:
    """Choose a stable display color for a category name."""

    normalized = normalize_text(category)
    if any(token in normalized for token in ("lesson", "resolved", "fix", "success")):
        return "green"
    if any(token in normalized for token in ("warn", "warning", "caution")):
        return "yellow"
    if any(
        token in normalized for token in ("error", "failure", "fatal", "exception", "crash", "bug")
    ):
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


def _event_path(errors_dir: Path, fingerprint: str) -> Path:
    return errors_dir / EVENTS_SUBDIR / f"{fingerprint}.json"


def load_structured_event(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_structured_event(errors_dir: Path, event: dict[str, Any]) -> Path:
    errors_dir.mkdir(parents=True, exist_ok=True)
    ev_dir = errors_dir / EVENTS_SUBDIR
    ev_dir.mkdir(parents=True, exist_ok=True)
    fp = str(event["fingerprint"])
    path = _event_path(errors_dir, fp)
    path.write_text(json.dumps(event, indent=2) + "\n", encoding="utf-8")
    return path


def iter_event_files(errors_dir: Path) -> list[Path]:
    ev = errors_dir / EVENTS_SUBDIR
    if not ev.is_dir():
        return []
    return sorted(p for p in ev.glob("*.json") if p.is_file())


def record_structured_event(
    errors_dir: Path,
    *,
    message: str,
    error_type: str | None = None,
    severity: str | None = None,
    root_cause: str | None = None,
    lesson: str = "",
    resolved: bool = False,
    source: str,
    source_path: str | None = None,
    line_number: int | None = None,
    category: str = "",
    timestamp: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """
    Persist or merge a structured error event. Returns (event, created_new_file).
    Deduplication uses fingerprint = hash(error_type, normalized message).
    """

    auto_type, auto_severity, auto_root_cause = categorize_error(message)
    et = error_type or auto_type
    sev = severity or auto_severity
    rc_out = root_cause or auto_root_cause

    fp = structured_fingerprint(et, message)
    now = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    path = _event_path(errors_dir, fp)

    if path.exists():
        existing = load_structured_event(path)
        existing["occurrence_count"] = int(existing.get("occurrence_count", 1)) + 1
        existing["last_seen"] = now
        if lesson and not existing.get("lesson"):
            existing["lesson"] = lesson
        if category and not existing.get("category"):
            existing["category"] = category
        save_structured_event(errors_dir, existing)
        write_summary_md(errors_dir)
        return existing, False

    event: dict[str, Any] = {
        "schema_version": STRUCTURED_SCHEMA_VERSION,
        "id": fp,
        "fingerprint": fp,
        "timestamp": now,
        "first_seen": now,
        "last_seen": now,
        "occurrence_count": 1,
        "error_type": et,
        "severity": sev,
        "root_cause": rc_out,
        "message": message.strip()[:8000],
        "lesson": lesson.strip(),
        "resolved": bool(resolved),
        "source": source,
        "source_path": source_path,
        "line_number": line_number,
        "category": category.strip(),
    }
    save_structured_event(errors_dir, event)
    write_summary_md(errors_dir)
    return event, True


def write_summary_md(errors_dir: Path) -> Path:
    """Regenerate aggregated stats and recommendations."""

    paths = iter_event_files(errors_dir)
    events = [load_structured_event(p) for p in paths]
    summary_path = errors_dir / "summary.md"
    errors_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    by_type: Counter[str] = Counter()
    occ_by_type: Counter[str] = Counter()
    for ev in events:
        et = str(ev.get("error_type", "unknown"))
        by_type[et] += 1
        occ_by_type[et] += int(ev.get("occurrence_count", 1))

    total_unique = len(events)
    total_occurrences = sum(int(ev.get("occurrence_count", 1)) for ev in events)

    # Top patterns by occurrence_count then message length
    ranked = sorted(
        events,
        key=lambda e: (-int(e.get("occurrence_count", 1)), str(e.get("last_seen", ""))),
    )[:12]

    lines = [
        "# Error learning summary",
        "",
        f"_Updated: {now}_",
        "",
        "## Totals",
        "",
        f"- **Unique error patterns:** {total_unique}",
        f"- **Recorded occurrences (sum):** {total_occurrences}",
        "",
        "## By error type",
        "",
        "| error_type | unique_patterns | occurrences |",
        "|------------|----------------:|------------:|",
    ]
    for et in sorted(by_type.keys(), key=lambda k: (-occ_by_type[k], k.lower())):
        lines.append(f"| `{et}` | {by_type[et]} | {occ_by_type[et]} |")

    lines.extend(["", "## Top failure patterns", ""])
    if not ranked:
        lines.append("_No structured events recorded yet._")
    else:
        for i, ev in enumerate(ranked, start=1):
            msg = str(ev.get("message", "")).replace("\n", " ").strip()
            if len(msg) > 160:
                msg = msg[:157] + "..."
            lines.append(
                f"{i}. **{ev.get('error_type', 'unknown')}** "
                f"(×{ev.get('occurrence_count', 1)}, {ev.get('severity', '')}) — {msg}"
            )

    lines.extend(["", "## Recommended fixes", ""])
    seen_types: set[str] = set()
    for et in sorted(occ_by_type.keys(), key=lambda k: -occ_by_type[k]):
        if et in seen_types:
            continue
        seen_types.add(et)
        fix = _RECOMMENDED_FIXES.get(et, _RECOMMENDED_FIXES["unknown"])
        lines.append(f"- **`{et}`:** {fix}")

    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary_path


def _extract_traceback_blocks(lines: list[str]) -> list[tuple[int, str]]:
    """Return (start_line_1based, block_text) for traceback-like regions."""

    results: list[tuple[int, str]] = []
    i = 0
    n = len(lines)
    while i < n:
        if re.search(r"^Traceback \(most recent call last\):", lines[i], re.I):
            start = i
            j = i + 1
            buf = [lines[i]]
            while j < n:
                buf.append(lines[j])
                if re.match(r"^\s*\w+(?:Error|Exception|Exit|Interrupt):\s*.+", lines[j]):
                    j += 1
                    break
                j += 1
            block = "\n".join(buf).strip()
            if block:
                results.append((start + 1, block))
            i = j
            continue
        i += 1
    return results


def parse_session_log_text(text: str, source_path: Path) -> list[dict[str, Any]]:
    """
    Extract error-like segments from OpenClaw-style session logs (markdown, plain text, JSONL-ish).
    """

    findings: list[dict[str, Any]] = []
    lines = text.splitlines()
    consumed_spans: set[tuple[int, int]] = set()

    for start_line, block in _extract_traceback_blocks(lines):
        findings.append(
            {
                "line_number": start_line,
                "message": block,
                "source_path": str(source_path),
            }
        )
        n_lines = len(block.splitlines())
        consumed_spans.add((start_line, start_line + n_lines))

    for idx, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        if not _ERROR_LINE_HINT.search(line):
            continue
        # Skip single-line duplicates inside an already captured traceback
        skip = False
        for a, b in consumed_spans:
            if a <= idx < b:
                skip = True
                break
        if skip:
            continue
        # Extend a few following lines for context if they look like continuation
        chunk = [line.rstrip()]
        for j in range(idx, min(idx + 4, len(lines))):
            if j == idx:
                continue
            nxt = lines[j].rstrip()
            if not nxt.strip():
                break
            if nxt.startswith(" ") or nxt.startswith("\t"):
                chunk.append(nxt)
            elif _ERROR_LINE_HINT.search(nxt):
                break
            else:
                chunk.append(nxt)
        msg = "\n".join(chunk).strip()
        if len(msg) < 8:
            continue
        findings.append({"line_number": idx, "message": msg, "source_path": str(source_path)})

    return findings


def discover_default_session_paths() -> list[Path]:
    """Best-effort OpenClaw workspace locations when the user omits explicit paths."""

    base = os.environ.get("OPENCLAW_WORKSPACE")
    root = Path(base).expanduser() if base else Path.home() / ".openclaw" / "workspace"
    if not root.is_dir():
        return []
    out: list[Path] = []
    for sub in ("logs", "memory", "sessions"):
        p = root / sub
        if p.is_dir():
            for pattern in ("*.log", "*.txt", "*.md"):
                out.extend(sorted(p.glob(pattern)))
    return out[:200]


def _expand_ingest_paths(paths: Iterable[Path], *, recursive: bool) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        p = raw.expanduser()
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            if recursive:
                for pattern in ("**/*.log", "**/*.md", "**/*.txt", "**/*.jsonl"):
                    files.extend(sorted(p.glob(pattern)))
            else:
                for pattern in ("*.log", "*.md", "*.txt", "*.jsonl"):
                    files.extend(sorted(p.glob(pattern)))
    # de-dupe, stable
    seen: set[str] = set()
    uniq: list[Path] = []
    for f in files:
        key = str(f.resolve())
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return uniq


def ingest_session_logs(
    errors_dir: Path,
    paths: Iterable[Path],
    *,
    recursive: bool = False,
) -> tuple[int, int, int]:
    """
    Parse session logs and record structured events. Returns (files_read, findings, new_events).
    """

    file_list = _expand_ingest_paths(paths, recursive=recursive)
    new_count = 0
    finding_total = 0
    for fp in file_list:
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        findings = parse_session_log_text(text, fp)
        finding_total += len(findings)
        for item in findings:
            _, created = record_structured_event(
                errors_dir,
                message=str(item["message"]),
                source="session_log",
                source_path=str(item.get("source_path")),
                line_number=item.get("line_number") if isinstance(item.get("line_number"), int) else None,
            )
            if created:
                new_count += 1
    if file_list:
        write_summary_md(errors_dir)
    return len(file_list), finding_total, new_count


def query_structured_events(
    errors_dir: Path,
    *,
    error_type: str | None = None,
    since: str | None = None,
    until: str | None = None,
    severity: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Filter structured events by type / ISO date prefix / severity."""

    since_day = since.strip()[:10] if since else ""
    until_day = until.strip()[:10] if until else ""
    et_filter = error_type.strip().lower() if error_type else ""
    sev_filter = severity.strip().lower() if severity else ""

    results: list[dict[str, Any]] = []
    for path in iter_event_files(errors_dir):
        ev = load_structured_event(path)
        if et_filter and str(ev.get("error_type", "")).lower() != et_filter:
            continue
        if sev_filter and str(ev.get("severity", "")).lower() != sev_filter:
            continue
        ts = str(ev.get("last_seen") or ev.get("timestamp") or "")
        day = ts[:10] if len(ts) >= 10 else ""
        if since_day and (not day or day < since_day):
            continue
        if until_day and (not day or day > until_day):
            continue
        results.append(ev)

    results.sort(key=lambda e: str(e.get("last_seen", e.get("timestamp", ""))), reverse=True)
    return results[: max(limit, 1)]


def add_entry(
    log_path: Path,
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
    errors_dir: Path | None = None,
) -> tuple[dict[str, object], bool]:
    """Add an error learning entry unless it already exists."""

    store = load_store(log_path)
    new_entry = build_entry(category, error, lesson, resolved=resolved)
    entries = store["entries"]
    assert isinstance(entries, list)
    for entry in entries:
        validated = validate_entry(entry)
        if entries_match(validated, new_entry):
            return validated, False

    entries.append(new_entry)
    entries.sort(key=lambda item: str(item["timestamp"]), reverse=True)
    save_store(log_path, store)

    ed = resolve_errors_dir(log_path, errors_dir)
    combined = f"{category}\n{error}"
    et, sev, rc = categorize_error(combined)
    record_structured_event(
        ed,
        message=error,
        error_type=et,
        severity=sev,
        root_cause=rc,
        lesson=lesson,
        resolved=resolved,
        source="manual",
        source_path=str(log_path),
        category=category,
        timestamp=str(new_entry["timestamp"]),
    )

    return new_entry, True


def format_entry(entry: dict[str, object]) -> str:
    """Render a single entry for console output."""

    category = str(entry["category"])
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    lines = [
        (
            f"{colorize(category, colorize_category(category))} "
            f"{colorize(f'[{status_text}]', status_color)} "
            f"{colorize(str(entry['timestamp']), 'cyan')}"
        ),
        f"  {colorize('ID:', 'yellow')} {entry['id']}",
        f"  {colorize('Error:', 'red')} {entry['error']}",
        f"  {colorize('Lesson:', 'green')} {entry['lesson']}",
    ]
    return "\n".join(lines)


def format_structured_event(ev: dict[str, Any]) -> str:
    """Render a structured JSON event for the CLI."""

    et = str(ev.get("error_type", "?"))
    sev = str(ev.get("severity", "?"))
    lines = [
        f"{colorize(et, 'red')} {colorize(f'[{sev}]', 'yellow')} "
        f"{colorize(str(ev.get('last_seen', ev.get('timestamp', ''))), 'cyan')}",
        f"  {colorize('fingerprint:', 'yellow')} {ev.get('fingerprint', ev.get('id'))}",
        f"  {colorize('count:', 'yellow')} {ev.get('occurrence_count', 1)}",
        f"  {colorize('source:', 'yellow')} {ev.get('source', '')}",
        f"  {colorize('root_cause:', 'green')} {ev.get('root_cause', '')}",
        f"  {colorize('message:', 'red')} {str(ev.get('message', ''))[:500]}",
    ]
    if ev.get("source_path"):
        lines.append(f"  {colorize('path:', 'cyan')} {ev['source_path']}")
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


def print_structured_events(events: list[dict[str, Any]], *, heading: str) -> None:
    print(colorize(heading, "bold"))
    print(colorize("=" * len(heading), "cyan"))
    if not events:
        print(colorize("No structured events matched.", "yellow"))
        return
    for i, ev in enumerate(events):
        if i:
            print()
        print(format_structured_event(ev))


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
            f"- {colorize(category, colorize_category(category))}: "
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
    parser.add_argument(
        "--errors-dir",
        type=Path,
        default=None,
        help="Structured events directory (default: <log-dir>/errors).",
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

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Parse OpenClaw session logs and record structured error events.",
    )
    ingest_parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Log files or directories (default: discover under OPENCLAW_WORKSPACE or ~/.openclaw/workspace).",
    )
    ingest_parser.add_argument(
        "--recursive",
        action="store_true",
        help="When a path is a directory, scan recursively for logs.",
    )

    query_parser = subparsers.add_parser("query", help="Query structured error events by filters.")
    query_parser.add_argument("--type", dest="error_type", default=None, help="Filter by error_type.")
    query_parser.add_argument("--since", default=None, help="Include events on/after this date (YYYY-MM-DD).")
    query_parser.add_argument("--until", default=None, help="Include events on/before this date (YYYY-MM-DD).")
    query_parser.add_argument("--severity", default=None, help="Filter by severity (e.g. high, medium, low).")
    query_parser.add_argument("--limit", type=int, default=50, help="Max events to print.")

    rebuild_parser = subparsers.add_parser(
        "rebuild-summary",
        help="Regenerate .learnings/errors/summary.md from stored JSON events.",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the error learning CLI."""

    args = parse_args(argv)
    errors_dir = resolve_errors_dir(args.log_path, args.errors_dir)

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
                errors_dir=args.errors_dir,
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

    if args.command == "ingest":
        paths = list(args.paths) if args.paths else discover_default_session_paths()
        if not paths:
            print(
                colorize(
                    "No log paths provided and no default OpenClaw workspace logs found.",
                    "yellow",
                ),
                file=sys.stderr,
            )
            return 1
        files_read, findings, new_ev = ingest_session_logs(
            errors_dir, paths, recursive=args.recursive
        )
        print(
            colorize(
                f"Scanned {files_read} file(s), extracted {findings} finding(s), "
                f"recorded {new_ev} new unique error(s). Summary: {errors_dir / 'summary.md'}",
                "green",
            )
        )
        return 0

    if args.command == "query":
        events = query_structured_events(
            errors_dir,
            error_type=args.error_type,
            since=args.since,
            until=args.until,
            severity=args.severity,
            limit=max(args.limit, 1),
        )
        print_structured_events(events, heading="Structured error events")
        return 0

    if args.command == "rebuild-summary":
        path = write_summary_md(errors_dir)
        print(colorize(f"Wrote {path}", "green"))
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
