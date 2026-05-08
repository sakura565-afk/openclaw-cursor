"""Error capture, categorization, and memory integration for OpenClaw."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from src.coordination.cross_bot_sync import atomic_write_text, normalize_memory_key, parse_memory_entries


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# Ordered rules: first match wins (most specific first).
_CATEGORY_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("syntax", re.compile(r"syntaxerror|invalid syntax|unexpected eof|indentationerror", re.I)),
    ("import", re.compile(r"importerror|modulenotfounderror|cannot import|no module named", re.I)),
    ("auth", re.compile(r"\b401\b|\b403\b|unauthorized|forbidden|authentication failed|invalid token|bad credentials", re.I)),
    ("network", re.compile(
        r"connection refused|connection reset|econnrefused|enotfound|etimedout|"
        r"timeout|timed out|urllib\.error|httperror|ssl\.|certificate|"
        r"\b5\d\d\b|network unreachable|name or service not known",
        re.I,
    )),
    ("resource", re.compile(r"memoryerror|out of memory|enospc|disk full|no space left", re.I)),
    ("permission", re.compile(r"permission denied|eacces|eperm|operation not permitted", re.I)),
    ("validation", re.compile(r"valueerror|assertionerror|typeerror|keyerror|validation failed|invalid argument", re.I)),
    ("tool_execution", re.compile(
        r"subprocess|exit code|command failed|returncode|non-zero exit|exec format error",
        re.I,
    )),
    ("runtime", re.compile(r"traceback|exception|runtimeerror|failed|failure|error\b", re.I)),
]

_ERROR_SIGNAL = re.compile(
    r"\b(error|exception|traceback|failed|failure|errno|fatal)\b",
    re.I,
)


def default_learnings_path() -> Path:
    override = os.environ.get("OPENCLAW_ERROR_LEARNINGS_PATH")
    if override:
        return Path(override).expanduser()
    home = Path(os.environ.get("OPENCLAW_HOME", Path.home() / ".openclaw")).expanduser()
    return home / "workspace" / "error_learnings.json"


def default_log_roots() -> list[Path]:
    home = Path(os.environ.get("OPENCLAW_HOME", Path.home() / ".openclaw")).expanduser()
    return [home / "logs", home / "workspace" / "logs"]


def default_memory_path() -> Path:
    override = os.environ.get("OPENCLAW_MEMORY_PATH")
    if override:
        return Path(override).expanduser()
    return Path.cwd() / "MEMORY.md"


def normalize_for_fingerprint(line: str) -> str:
    """Collapse volatile tokens so similar errors share one fingerprint."""
    cleaned = line.strip()
    cleaned = re.sub(r"^\[[^\]]+\]\s*", "", cleaned)
    cleaned = re.sub(r"^\d{4}-\d{2}-\d{2}[T ][^ ]+\s*", "", cleaned)
    cleaned = re.sub(
        r"\b(?:0x[0-9a-f]+|\d[\d,]*)\b",
        "#",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"/[^\s]+", "/#", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:240] or "unspecified"


def classify_error_line(line: str) -> str:
    """Return a coarse category name for the line."""
    for name, pattern in _CATEGORY_RULES:
        if pattern.search(line):
            return name
    return "unknown"


def stable_fingerprint_id(category: str, normalized: str) -> str:
    raw = f"{category}|{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def iter_log_lines(log_roots: list[Path]) -> Iterator[tuple[str, str]]:
    """Yield (source_label, line) for text and JSON logs."""
    for root in log_roots:
        if not root.exists():
            continue
        for log_file in sorted(root.rglob("*")):
            if not log_file.is_file():
                continue
            text = _read_text_file(log_file)
            if not text:
                continue
            rel = str(log_file)
            if log_file.suffix == ".json":
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    yield rel, text[:2000]
                    continue
                if isinstance(payload, list):
                    for item in payload:
                        yield rel, json.dumps(item, sort_keys=True)
                    continue
                if isinstance(payload, dict):
                    yield rel, json.dumps(payload, sort_keys=True)
                    continue
            for line in text.splitlines():
                yield rel, line


@dataclass
class ErrorLearningRecord:
    """One deduplicated error pattern with optional mitigation lesson."""

    fingerprint_id: str
    category: str
    normalized_message: str
    sample_line: str
    occurrences: int = 1
    first_seen: str = field(default_factory=utc_now_iso)
    last_seen: str = field(default_factory=utc_now_iso)
    sources: list[str] = field(default_factory=list)
    lesson: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ErrorLearningRecord:
        return cls(
            fingerprint_id=str(data["fingerprint_id"]),
            category=str(data["category"]),
            normalized_message=str(data["normalized_message"]),
            sample_line=str(data.get("sample_line", "")),
            occurrences=int(data.get("occurrences", 1)),
            first_seen=str(data.get("first_seen", utc_now_iso())),
            last_seen=str(data.get("last_seen", utc_now_iso())),
            sources=list(data.get("sources", [])),
            lesson=data.get("lesson"),
        )


def memory_line(record: ErrorLearningRecord) -> str:
    """Format a MEMORY.md bullet; first ':' separates stable key (see normalize_memory_key)."""
    tail = record.lesson.strip() if record.lesson else "Review logs and add mitigation when root cause is known."
    # Single ':' after category — remainder may contain colons safely.
    summary = record.normalized_message[:200]
    return f"- [err-{record.fingerprint_id}] {record.category}: {summary} — note: {tail}\n"


class ErrorLearningEngine:
    """Accumulate categorized errors and optionally sync lessons into MEMORY.md."""

    STORE_VERSION = 1

    def __init__(self, store_path: Path | None = None) -> None:
        self.store_path = Path(store_path) if store_path else default_learnings_path()
        self.records: dict[str, ErrorLearningRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.store_path.exists():
            return
        try:
            raw = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict) or raw.get("version") != self.STORE_VERSION:
            return
        bucket = raw.get("records")
        if not isinstance(bucket, dict):
            return
        for key, item in bucket.items():
            if isinstance(item, dict):
                try:
                    self.records[key] = ErrorLearningRecord.from_dict(item)
                except (KeyError, TypeError, ValueError):
                    continue

    def save(self) -> None:
        payload = {
            "version": self.STORE_VERSION,
            "updated_at": utc_now_iso(),
            "records": {k: v.to_dict() for k, v in sorted(self.records.items())},
        }
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.store_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def ingest_line(
        self,
        line: str,
        *,
        source: str,
        force_category: str | None = None,
        lesson: str | None = None,
        require_error_signal: bool = True,
    ) -> ErrorLearningRecord | None:
        """Ingest one log line; returns None if skipped as non-error noise."""
        if not line.strip():
            return None
        category = force_category or classify_error_line(line)
        if (
            require_error_signal
            and category == "unknown"
            and not _ERROR_SIGNAL.search(line)
        ):
            return None
        normalized = normalize_for_fingerprint(line)
        fp = stable_fingerprint_id(category, normalized)
        now = utc_now_iso()
        if fp in self.records:
            rec = self.records[fp]
            rec.occurrences += 1
            rec.last_seen = now
            if source and source not in rec.sources:
                rec.sources.append(source)
            if lesson:
                rec.lesson = lesson
            return rec
        rec = ErrorLearningRecord(
            fingerprint_id=fp,
            category=category,
            normalized_message=normalized,
            sample_line=line.strip()[:500],
            occurrences=1,
            first_seen=now,
            last_seen=now,
            sources=[source] if source else [],
            lesson=lesson,
        )
        self.records[fp] = rec
        return rec

    def ingest_lines(self, lines: Iterable[str], *, source: str, lesson: str | None = None) -> list[ErrorLearningRecord]:
        out: list[ErrorLearningRecord] = []
        for line in lines:
            rec = self.ingest_line(line, source=source, lesson=lesson)
            if rec:
                out.append(rec)
        return out

    def scan_logs(self, log_roots: list[Path] | None = None, *, max_lines: int | None = None) -> int:
        roots = log_roots if log_roots is not None else default_log_roots()
        seen = 0
        for src, line in iter_log_lines(roots):
            self.ingest_line(line, source=src)
            seen += 1
            if max_lines is not None and seen >= max_lines:
                break
        return seen

    def records_sorted(self) -> list[ErrorLearningRecord]:
        return sorted(
            self.records.values(),
            key=lambda r: (-r.occurrences, r.category, r.fingerprint_id),
        )

    def render_report(self) -> str:
        lines = [
            "# Error learning report",
            "",
            f"- Store: `{self.store_path}`",
            f"- Entries: {len(self.records)}",
            "",
            "## By category",
            "",
        ]
        by_cat: dict[str, list[ErrorLearningRecord]] = {}
        for rec in self.records.values():
            by_cat.setdefault(rec.category, []).append(rec)
        for cat in sorted(by_cat):
            lines.append(f"### {cat}")
            for rec in sorted(by_cat[cat], key=lambda r: -r.occurrences):
                lesson = rec.lesson or "(no lesson yet)"
                lines.append(
                    f"- **{rec.fingerprint_id}** ×{rec.occurrences}: `{rec.normalized_message[:120]}` — {lesson}"
                )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def export_json(self) -> dict[str, Any]:
        return {
            "version": self.STORE_VERSION,
            "updated_at": utc_now_iso(),
            "records": {k: v.to_dict() for k, v in sorted(self.records.items())},
        }

    def sync_memory(self, memory_path: Path | None = None, *, dry_run: bool = False) -> dict[str, Any]:
        """Append new learnings to MEMORY.md with deduplication via normalize_memory_key."""
        path = Path(memory_path) if memory_path else default_memory_path()
        existing_text = path.read_text(encoding="utf-8") if path.exists() else ""
        existing_keys = set(parse_memory_entries(existing_text).keys())
        added: list[str] = []
        for rec in self.records_sorted():
            line = memory_line(rec).rstrip("\n")
            key = normalize_memory_key(line)
            if not key or key in existing_keys:
                continue
            existing_keys.add(key)
            added.append(line + "\n")
        if added and not dry_run:
            section = "\n## Error learnings\n\n"
            if "## Error learnings" not in existing_text:
                payload = existing_text.rstrip() + "\n" + section + "".join(added)
            else:
                payload = existing_text.rstrip() + "\n" + "".join(added)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, payload if payload.endswith("\n") else payload + "\n")
        return {"memory_path": str(path), "added_lines": len(added), "dry_run": dry_run}
