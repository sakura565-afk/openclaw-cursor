"""Capture tool and agent errors, categorize them, and persist lessons for MEMORY.md.

This module complements :mod:`cross_bot_sync` by turning raw log lines into
structured records (JSONL) and human-readable bullets under a dedicated
``MEMORY.md`` section. Duplicates are suppressed using the same normalization
idea as memory entry keys.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Literal, Sequence

from .cross_bot_sync import (
    DEFAULT_MEMORY_FILE,
    FileLock,
    LockTimeoutError,
    atomic_write_text,
    normalize_memory_key,
    utc_now,
)

Severity = Literal["debug", "info", "warning", "error", "critical"]
ErrorCategory = Literal[
    "network",
    "authentication",
    "permission",
    "timeout",
    "tool_execution",
    "syntax",
    "import_module",
    "file_io",
    "subprocess",
    "api_rate_limit",
    "configuration",
    "runtime",
    "unknown",
]

DEFAULT_SECTION_HEADING = "Error learnings"

# Ordered: first match wins (specific before generic).
_CATEGORY_RULES: list[tuple[re.Pattern[str], ErrorCategory]] = [
    (
        re.compile(
            r"(?i)(?:401|403|unauthorized|forbidden|invalid[_\s]?token|"
            r"api[_\s]?key|authentication failed|not authenticated)"
        ),
        "authentication",
    ),
    (
        re.compile(
            r"(?i)(?:permission denied|eacces|eperm|operation not permitted|"
            r"cannot access|access is denied)"
        ),
        "permission",
    ),
    (
        re.compile(
            r"(?i)(?:rate.?limit|429|too many requests|quota exceeded|"
            r"throttl)"
        ),
        "api_rate_limit",
    ),
    (
        re.compile(
            r"(?i)(?:econnrefused|econnreset|network unreachable|"
            r"connection (?:refused|reset|timed out)|failed to connect|"
            r"ssl(?:\.|\s*)error|certificate|tls|getaddrinfo|"
            r"name or service not known)"
        ),
        "network",
    ),
    (
        re.compile(
            r"(?i)(?:etimedout|timed?\s*out|timeout|deadline exceeded|"
            r"gateway timeout|504)"
        ),
        "timeout",
    ),
    (
        re.compile(
            r"(?i)(?:modulenotfounderror|importerror|no module named|"
            r"cannot import name)"
        ),
        "import_module",
    ),
    (
        re.compile(r"(?i)(?:syntaxerror|indentationerror|taberror)"),
        "syntax",
    ),
    (
        re.compile(
            r"(?i)(?:filenotfounderror|enoent|is a directory|not a directory|"
            r"errno\s*2\b)"
        ),
        "file_io",
    ),
    (
        re.compile(
            r"(?i)(?:subprocess|command failed|exit code|non-zero exit|"
            r"process exited)"
        ),
        "subprocess",
    ),
    (
        re.compile(
            r"(?i)(?:mcp\b|tool (?:call|execution)|function_call|"
            r"invalid_tool|tool_error)"
        ),
        "tool_execution",
    ),
    (
        re.compile(
            r"(?i)(?:config(?:uration)?|environment variable|missing env|"
            r"invalid argument|keyerror:.*config)"
        ),
        "configuration",
    ),
    (
        re.compile(
            r"(?i)(?:typeerror|valueerror|attributeerror|keyerror|"
            r"indexerror|runtimeerror|assertionerror|recursionerror|"
            r"zerodivisionerror|stopiteration)"
        ),
        "runtime",
    ),
]


def default_jsonl_path() -> Path:
    """Return the default JSONL store under ``~/.openclaw/errors/``."""

    base = Path(os.environ.get("OPENCLAW_HOME", Path.home() / ".openclaw"))
    override = os.environ.get("OPENCLAW_ERROR_LEARNINGS_PATH")
    if override:
        return Path(override).expanduser()
    return (base / "errors" / "learned_errors.jsonl").resolve()


def infer_severity(text: str) -> Severity:
    """Infer a coarse severity from log text."""

    lower = text.lower()
    if any(x in lower for x in ("critical", "panic", "fatal")):
        return "critical"
    if "warning" in lower or "warn" in lower:
        return "warning"
    if any(x in lower for x in ("error", "exception", "failed", "failure")):
        return "error"
    if "debug" in lower:
        return "debug"
    return "info"


def categorize_error(text: str) -> ErrorCategory:
    """Assign a high-level category using regex rules."""

    for pattern, category in _CATEGORY_RULES:
        if pattern.search(text):
            return category
    return "unknown"


def fingerprint_excerpt(text: str, max_chars: int = 2048) -> str:
    """Stable fingerprint for deduplication (SHA-256 hex)."""

    normalized = re.sub(r"\s+", " ", text.strip().lower())[:max_chars]
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()


@dataclass(frozen=True)
class ParsedSignal:
    """One extracted error-like signal from raw text."""

    excerpt: str
    category: ErrorCategory
    severity: Severity
    line_hint: int | None


@dataclass
class LearningRecord:
    """Structured row appended to the JSONL store."""

    ts: str
    category: ErrorCategory
    severity: Severity
    fingerprint: str
    source: str
    raw_excerpt: str
    lesson: str
    line_hint: int | None = None

    def to_json_line(self) -> str:
        payload = asdict(self)
        return json.dumps(payload, ensure_ascii=False) + "\n"


def _truncate(text: str, max_len: int = 320) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def build_lesson(category: ErrorCategory, excerpt: str) -> str:
    """Turn raw text into a short, actionable line for MEMORY.md."""

    one_line = re.sub(r"\s+", " ", excerpt.splitlines()[0] if excerpt else "").strip()
    one_line = _truncate(one_line, 200)
    hints = {
        "network": "Verify connectivity, DNS, and TLS; retry with backoff.",
        "authentication": "Check credentials, tokens, and environment.",
        "permission": "Confirm file and API permissions for this user.",
        "timeout": "Increase limits or reduce workload; check upstream health.",
        "tool_execution": "Validate tool parameters and availability.",
        "syntax": "Fix the reported syntax before re-running.",
        "import_module": "Install missing packages or fix PYTHONPATH.",
        "file_io": "Confirm paths exist and are readable/writable.",
        "subprocess": "Inspect the command, cwd, and captured stderr.",
        "api_rate_limit": "Throttle requests or raise quotas.",
        "configuration": "Align environment variables and config files.",
        "runtime": "Inspect types, keys, and invariants around the failure.",
        "unknown": "Reproduce with logging and narrow the failing step.",
    }
    tail = hints.get(category, hints["unknown"])
    if one_line:
        return f"{one_line} — {tail}"
    return tail


def format_memory_bullet(
    category: ErrorCategory,
    lesson: str,
    ts: str | None = None,
) -> str:
    """Format a single MEMORY.md bullet (list item)."""

    day = (ts or utc_now())[:10]
    safe_lesson = _truncate(lesson, 400)
    return f"- **{category}** ({day}): {safe_lesson}"


_ERR_LINE = re.compile(
    r"(?i)(\berror\b|\bexception\b|critical|fatal|traceback|\[-\]|\[x\]\s*error)"
)
_TRACE_END = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*(?:Error|Warning):\s*.+")


def _parse_section_bullets(text: str) -> dict[str, str]:
    """Map normalized keys to raw lines for markdown list items only."""

    entries: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not re.match(r"^[-*+]\s+", line):
            continue
        key = normalize_memory_key(line)
        if key:
            entries[key] = line
    return entries


def extract_error_signals(text: str, source_name: str = "text") -> list[ParsedSignal]:
    """Scan *text* line-by-line for error-like patterns and exception blocks."""

    lines = text.splitlines()
    out: list[ParsedSignal] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        if stripped.lower().startswith("traceback (most recent call last):"):
            block: list[str] = [lines[i]]
            j = i + 1
            while j < len(lines) and j < i + 80:
                block.append(lines[j])
                if _TRACE_END.match(lines[j].strip()):
                    j += 1
                    break
                j += 1
            excerpt = "\n".join(block)
            cat = categorize_error(excerpt)
            sev = infer_severity(excerpt)
            out.append(
                ParsedSignal(
                    excerpt=_truncate(excerpt, 4000),
                    category=cat,
                    severity=sev,
                    line_hint=i + 1,
                )
            )
            i = j
            continue

        if _ERR_LINE.search(line) or ": error:" in line.lower():
            window = "\n".join(lines[max(0, i - 2) : i + 3])
            cat = categorize_error(window)
            sev = infer_severity(window)
            out.append(
                ParsedSignal(
                    excerpt=_truncate(window, 2000),
                    category=cat,
                    severity=sev,
                    line_hint=i + 1,
                )
            )
        i += 1

    # De-duplicate by fingerprint while preserving order.
    seen: set[str] = set()
    deduped: list[ParsedSignal] = []
    for sig in out:
        fp = fingerprint_excerpt(sig.excerpt)
        if fp in seen:
            continue
        seen.add(fp)
        deduped.append(sig)
    return deduped


def load_existing_fingerprints(jsonl_path: Path, limit_lines: int = 500_000) -> set[str]:
    """Load fingerprints from existing JSONL for skip-if-seen behavior."""

    if not jsonl_path.exists():
        return set()
    found: set[str] = set()
    count = 0
    with jsonl_path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            count += 1
            if count > limit_lines:
                break
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            fp = row.get("fingerprint")
            if isinstance(fp, str):
                found.add(fp)
    return found


def append_jsonl(path: Path, record: LearningRecord) -> None:
    """Append one JSON line to *path* (creates parent dirs)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.to_json_line())


def _split_memory_sections(content: str, heading: str) -> tuple[str, str, str]:
    """Return (before, section_body, after) for ``## {heading}``."""

    pattern = re.compile(
        rf"^(\s*)(#{{1,6}})\s+{re.escape(heading)}\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    match = pattern.search(content)
    if not match:
        return content, "", ""

    start = match.start()
    level = len(match.group(2))
    # Next heading of same or higher level (smaller # count)
    rest = content[match.end() :]
    next_heading = re.compile(rf"^(\s*)#{{1,{level}}}\s+\S", re.MULTILINE)
    nm = next_heading.search(rest)
    if nm:
        body_end = nm.start()
        body = rest[:body_end]
        after = rest[body_end:]
    else:
        body = rest
        after = ""
    before = content[:start]
    return before, body, after


def merge_lesson_into_memory(
    memory_text: str,
    bullet_line: str,
    section_heading: str = DEFAULT_SECTION_HEADING,
) -> tuple[str, bool]:
    """Insert *bullet_line* under ``## {section_heading}`` if not duplicate.

    Returns ``(new_content, changed)``.
    """

    key = normalize_memory_key(bullet_line)
    if not key:
        return memory_text, False

    before, body, after = _split_memory_sections(memory_text, section_heading)
    if not body and not re.search(
        rf"(?im)^#{{1,6}}\s+{re.escape(section_heading)}\s*$", memory_text
    ):
        # Section missing: append new section at end.
        sep = "" if memory_text.endswith("\n") else "\n"
        block = f"{sep}## {section_heading}\n\n{bullet_line}\n"
        combined = memory_text + block
        return combined, True

    # Parse existing bullets in section body for dedup.
    existing_keys = set(_parse_section_bullets(body).keys()) if body.strip() else set()
    if key in existing_keys:
        return memory_text, False

    new_body = (body.rstrip() + "\n" + bullet_line + "\n") if body.strip() else bullet_line + "\n"
    heading_line = f"## {section_heading}\n\n"
    rebuilt = before + heading_line + new_body + after
    return rebuilt, True


def register_learning(
    *,
    excerpt: str,
    category: ErrorCategory | None = None,
    severity: Severity | None = None,
    source: str = "manual",
    jsonl_path: Path | None = None,
    memory_path: Path | None = None,
    section_heading: str = DEFAULT_SECTION_HEADING,
    skip_if_seen: bool = True,
    write_memory: bool = True,
) -> LearningRecord | None:
    """Persist one learning to JSONL and optionally MEMORY.md.

    Returns ``None`` if skipped as duplicate.
    """

    cat = category or categorize_error(excerpt)
    sev = severity or infer_severity(excerpt)
    fp = fingerprint_excerpt(excerpt)
    lesson = build_lesson(cat, excerpt)
    store = jsonl_path or default_jsonl_path()
    if skip_if_seen and fp in load_existing_fingerprints(store):
        return None

    record = LearningRecord(
        ts=utc_now(),
        category=cat,
        severity=sev,
        fingerprint=fp,
        source=source,
        raw_excerpt=_truncate(excerpt, 8000),
        lesson=lesson,
        line_hint=None,
    )
    append_jsonl(store, record)

    mem = memory_path or Path(
        os.environ.get("OPENCLAW_MEMORY_PATH", str(DEFAULT_MEMORY_FILE))
    )
    if write_memory:
        lock_path = mem.parent / f".{mem.name}.error_learning.lock"
        bullet = format_memory_bullet(cat, lesson, record.ts)
        try:
            with FileLock(lock_path, timeout=float(os.environ.get("OPENCLAW_ERROR_LEARNING_LOCK_TIMEOUT", "30"))):
                text = mem.read_text(encoding="utf-8", errors="replace") if mem.exists() else ""
                new_text, changed = merge_lesson_into_memory(
                    text, bullet, section_heading=section_heading
                )
                if changed:
                    atomic_write_text(mem, new_text)
        except OSError:
            # Still keep JSONL; memory can be retried.
            pass
        except LockTimeoutError:
            pass

    return record


def process_signals(
    signals: Sequence[ParsedSignal],
    *,
    source: str,
    jsonl_path: Path | None = None,
    memory_path: Path | None = None,
    section_heading: str = DEFAULT_SECTION_HEADING,
    skip_if_seen: bool = True,
    write_memory: bool = True,
) -> tuple[int, int]:
    """Register each signal; returns ``(written, skipped)``."""

    store = jsonl_path or default_jsonl_path()
    existing = load_existing_fingerprints(store) if skip_if_seen else set()
    written = 0
    skipped = 0
    mem = memory_path or Path(
        os.environ.get("OPENCLAW_MEMORY_PATH", str(DEFAULT_MEMORY_FILE))
    )
    pending_bullets: list[tuple[str, str, ErrorCategory]] = []

    for sig in signals:
        fp = fingerprint_excerpt(sig.excerpt)
        if skip_if_seen and fp in existing:
            skipped += 1
            continue

        ts = utc_now()
        record = LearningRecord(
            ts=ts,
            category=sig.category,
            severity=sig.severity,
            fingerprint=fp,
            source=source,
            raw_excerpt=_truncate(sig.excerpt, 8000),
            lesson=build_lesson(sig.category, sig.excerpt),
            line_hint=sig.line_hint,
        )
        append_jsonl(store, record)
        existing.add(fp)
        written += 1
        pending_bullets.append((ts, record.lesson, sig.category))

    if write_memory and pending_bullets:
        lock_path = mem.parent / f".{mem.name}.error_learning.lock"
        try:
            with FileLock(
                lock_path,
                timeout=float(os.environ.get("OPENCLAW_ERROR_LEARNING_LOCK_TIMEOUT", "30")),
            ):
                text = mem.read_text(encoding="utf-8", errors="replace") if mem.exists() else ""
                new_text = text
                any_change = False
                for ts, lesson, cat in pending_bullets:
                    bullet = format_memory_bullet(cat, lesson, ts)
                    new_text, changed = merge_lesson_into_memory(
                        new_text, bullet, section_heading=section_heading
                    )
                    any_change = any_change or changed
                if any_change:
                    atomic_write_text(mem, new_text)
        except (OSError, LockTimeoutError):
            pass

    return written, skipped


def iter_text_inputs(paths: Sequence[Path]) -> Iterator[tuple[str, str]]:
    """Yield ``(source_label, text)`` for each existing file path."""

    for path in paths:
        p = Path(path)
        if not p.is_file():
            continue
        try:
            yield str(p), p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue


__all__ = [
    "DEFAULT_SECTION_HEADING",
    "ErrorCategory",
    "LearningRecord",
    "ParsedSignal",
    "Severity",
    "append_jsonl",
    "build_lesson",
    "categorize_error",
    "default_jsonl_path",
    "extract_error_signals",
    "fingerprint_excerpt",
    "format_memory_bullet",
    "infer_severity",
    "iter_text_inputs",
    "load_existing_fingerprints",
    "merge_lesson_into_memory",
    "process_signals",
    "register_learning",
]
