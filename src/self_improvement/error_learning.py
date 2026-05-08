"""Persistent error capture, categorization, and pattern learning for self-improvement.

Stores normalized error signatures so recurring failures surface as aggregated patterns
with counts and timestamps. Designed for append-heavy workloads with atomic writes and
optional OS-level file locking on POSIX.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, Iterable, Iterator, List, Optional, Sequence

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows
    fcntl = None


SCHEMA_VERSION = 1
DEFAULT_MAX_PATTERNS = 2000
DEFAULT_MAX_SAMPLES = 5
STORE_FILENAME = "error_patterns_v1.json"


class ErrorCategory(str, Enum):
    NETWORK = "network"
    TIMEOUT = "timeout"
    AUTH_PERMISSION = "auth_permission"
    RESOURCE_DISK = "resource_disk"
    RESOURCE_MEMORY = "resource_memory"
    HTTP_CLIENT = "http_client"
    SUBPROCESS = "subprocess"
    SYNTAX = "syntax"
    IMPORT = "import"
    TYPE_VALUE = "type_value"
    IO_FILE = "io_file"
    DATABASE = "database"
    CONFIG = "config"
    UNKNOWN = "unknown"


# Ordered rules: first match wins (most specific patterns first).
_RULES: List[tuple[re.Pattern[str], ErrorCategory]] = [
    (
        re.compile(
            r"\b(syntaxerror|indentationerror|invalid syntax|unexpected indent|unexpected eof)\b",
            re.IGNORECASE,
        ),
        ErrorCategory.SYNTAX,
    ),
    (
        re.compile(
            r"\b(importerror|modulenotfounderror|no module named|cannot import)\b",
            re.IGNORECASE,
        ),
        ErrorCategory.IMPORT,
    ),
    (
        re.compile(
            r"\b(typeerror|valueerror|keyerror|attributeerror|assertionerror|indexerror|"
            r"zerodivisionerror)\b",
            re.IGNORECASE,
        ),
        ErrorCategory.TYPE_VALUE,
    ),
    (
        re.compile(
            r"\b(filenotfounderror|isadirectoryerror|not a directory|errno\s*2\b|enoent)\b",
            re.IGNORECASE,
        ),
        ErrorCategory.IO_FILE,
    ),
    (
        re.compile(
            r"\b(permission denied|eacces|eperm|forbidden|401|403|unauthorized)\b",
            re.IGNORECASE,
        ),
        ErrorCategory.AUTH_PERMISSION,
    ),
    (
        re.compile(
            r"\b(no space left|enospc|disk full|quota exceeded)\b",
            re.IGNORECASE,
        ),
        ErrorCategory.RESOURCE_DISK,
    ),
    (
        re.compile(
            r"\b(cannot allocate memory|enomem|out of memory|oom)\b",
            re.IGNORECASE,
        ),
        ErrorCategory.RESOURCE_MEMORY,
    ),
    (
        re.compile(
            r"\b(timeout|timed out|deadline exceeded|etimedout)\b",
            re.IGNORECASE,
        ),
        ErrorCategory.TIMEOUT,
    ),
    (
        re.compile(
            r"\b(econnrefused|econnreset|enotfound|enetunreach|connection reset|"
            r"connection refused|name or service not known|getaddrinfo failed|"
            r"network is unreachable|broken pipe|ssl error|tls)\b",
            re.IGNORECASE,
        ),
        ErrorCategory.NETWORK,
    ),
    (
        re.compile(r"\b(http\s*(?:error|status)?\s*[45]\d{2}|bad gateway|service unavailable)\b", re.IGNORECASE),
        ErrorCategory.HTTP_CLIENT,
    ),
    (
        re.compile(
            r"\b(operationalerror|database error|sqlite|postgres|mysql|mongodb)\b",
            re.IGNORECASE,
        ),
        ErrorCategory.DATABASE,
    ),
    (
        re.compile(
            r"\b(configparser|yaml\.error|json\.decode|invalid json|configuration error)\b",
            re.IGNORECASE,
        ),
        ErrorCategory.CONFIG,
    ),
    (
        re.compile(r"\b(returncode|exit code|command failed|subprocess)\b", re.IGNORECASE),
        ErrorCategory.SUBPROCESS,
    ),
]


_EXCEPTION_ALIASES: Dict[str, ErrorCategory] = {
    "SyntaxError": ErrorCategory.SYNTAX,
    "IndentationError": ErrorCategory.SYNTAX,
    "TabError": ErrorCategory.SYNTAX,
    "ImportError": ErrorCategory.IMPORT,
    "ModuleNotFoundError": ErrorCategory.IMPORT,
    "FileNotFoundError": ErrorCategory.IO_FILE,
    "IsADirectoryError": ErrorCategory.IO_FILE,
    "NotADirectoryError": ErrorCategory.IO_FILE,
    "PermissionError": ErrorCategory.AUTH_PERMISSION,
    "TimeoutError": ErrorCategory.TIMEOUT,
    "ConnectionError": ErrorCategory.NETWORK,
    "BlockingIOError": ErrorCategory.NETWORK,
    "OSError": ErrorCategory.IO_FILE,
    "MemoryError": ErrorCategory.RESOURCE_MEMORY,
    "ZeroDivisionError": ErrorCategory.TYPE_VALUE,
    "json.JSONDecodeError": ErrorCategory.CONFIG,
}


_PATHLIKE = re.compile(
    r"(?:/[\w.\-]+)+|\\(?:[\w.\-]+\\)+|[A-Za-z]:\\(?:[\w.\-]+\\)+",
)
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_EMAILISH = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_error_text(text: str, *, max_length: int = 400) -> str:
    """Collapse noisy literals so semantically similar errors share one signature."""
    cleaned = text.strip()
    cleaned = re.sub(r"^\[[^\]]+\]\s*", "", cleaned)
    cleaned = re.sub(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s]*\s*", "", cleaned)
    cleaned = _PATHLIKE.sub("<path>", cleaned)
    cleaned = _UUID.sub("<uuid>", cleaned)
    cleaned = _IP.sub("<ip>", cleaned)
    cleaned = _EMAILISH.sub("<email>", cleaned)
    cleaned = re.sub(r"\b0x[0-9a-f]+\b", "<hex>", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d+\b", "#", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:max_length] if cleaned else "unspecified_error"


def categorize_error(text: str, exception_type: Optional[str] = None) -> ErrorCategory:
    if exception_type:
        mapped = _EXCEPTION_ALIASES.get(exception_type)
        if mapped:
            return mapped
        # Qualified names like json.JSONDecodeError
        short = exception_type.split(".")[-1]
        mapped = _EXCEPTION_ALIASES.get(short)
        if mapped:
            return mapped
    haystack = text
    for pattern, category in _RULES:
        if pattern.search(haystack):
            return category
    return ErrorCategory.UNKNOWN


def signature_for(normalized: str) -> str:
    digest = hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()
    return digest[:16]


@dataclass
class AggregatedPattern:
    """One learned cluster of similar errors."""

    signature: str
    category: ErrorCategory
    normalized_text: str
    count: int
    first_seen: str
    last_seen: str
    samples: List[str] = field(default_factory=list)
    sources: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "signature": self.signature,
            "category": self.category.value,
            "normalized_text": self.normalized_text,
            "count": self.count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "samples": self.samples,
            "sources": dict(sorted(self.sources.items(), key=lambda kv: (-kv[1], kv[0]))),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> AggregatedPattern:
        cat_raw = raw.get("category", "unknown")
        try:
            category = ErrorCategory(cat_raw)
        except ValueError:
            category = ErrorCategory.UNKNOWN
        return cls(
            signature=str(raw.get("signature", "")),
            category=category,
            normalized_text=str(raw.get("normalized_text", "")),
            count=int(raw.get("count", 0)),
            first_seen=str(raw.get("first_seen", _utc_now().isoformat())),
            last_seen=str(raw.get("last_seen", _utc_now().isoformat())),
            samples=list(raw.get("samples") or []) if isinstance(raw.get("samples"), list) else [],
            sources=dict(raw.get("sources") or {}) if isinstance(raw.get("sources"), dict) else {},
        )


def _atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class _FileLock:
    """Best-effort POSIX advisory lock; no-op when fcntl is unavailable."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: Optional[BinaryIO] = None

    def __enter__(self) -> None:
        if fcntl is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+b")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            self._fh.close()
            self._fh = None

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._fh is None:
            return
        try:
            if fcntl:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None


class ErrorLearningSystem:
    """Records errors, aggregates by normalized signature, persists state."""

    def __init__(
        self,
        storage_dir: Path | str | None = None,
        *,
        max_patterns: int = DEFAULT_MAX_PATTERNS,
        max_samples_per_pattern: int = DEFAULT_MAX_SAMPLES,
        clock: Callable[[], datetime] | None = None,
        append_audit_log: bool = True,
    ) -> None:
        root = Path(storage_dir or Path.cwd() / "logs" / "error_learning")
        self.storage_dir = root
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.store_path = self.storage_dir / STORE_FILENAME
        self.lock_path = self.storage_dir / ".error_learning.lock"
        self.max_patterns = max(1, max_patterns)
        self.max_samples = max(1, max_samples_per_pattern)
        self._clock = clock or _utc_now
        self.append_audit_log = append_audit_log

    def _read_patterns(self) -> Dict[str, AggregatedPattern]:
        with _FileLock(self.lock_path):
            return self._load_patterns_unlocked()

    def record(
        self,
        message: str,
        *,
        exception_type: Optional[str] = None,
        source: str = "unknown",
        extra: Optional[Dict[str, Any]] = None,
    ) -> AggregatedPattern:
        """Ingest one error message; returns the updated aggregate row."""
        normalized = normalize_error_text(message)
        category = categorize_error(message, exception_type)
        sig = signature_for(normalized)
        now = self._clock().isoformat()

        with _FileLock(self.lock_path):
            patterns = self._load_patterns_unlocked()
            existing = patterns.get(sig)
            if existing:
                existing.count += 1
                existing.last_seen = now
                existing.category = category  # upgrade category if parser improves
                self._add_sample(existing, message[:500])
                existing.sources[source] = existing.sources.get(source, 0) + 1
            else:
                existing = AggregatedPattern(
                    signature=sig,
                    category=category,
                    normalized_text=normalized,
                    count=1,
                    first_seen=now,
                    last_seen=now,
                    samples=[message[:500]],
                    sources={source: 1},
                )
                patterns[sig] = existing

            self._prune_if_needed(patterns)
            self._save_patterns_unlocked(patterns)
            recorded = patterns[sig]

        if self.append_audit_log:
            self._append_audit_event(
                {
                    "timestamp": now,
                    "signature": sig,
                    "category": category.value,
                    "source": source,
                    "exception_type": exception_type,
                    "extra": extra or {},
                    "snippet": message[:400],
                }
            )
        return recorded

    def record_exception(self, exc: BaseException, *, source: str = "exception", extra: Optional[Dict[str, Any]] = None) -> AggregatedPattern:
        name = type(exc).__name__
        parts = [f"{name}: {exc}"]
        if exc.__cause__:
            parts.append(f"caused by {type(exc.__cause__).__name__}: {exc.__cause__}")
        return self.record("\n".join(parts), exception_type=name, source=source, extra=extra)

    def record_subprocess(
        self,
        result: subprocess.CompletedProcess[str],
        *,
        source: str = "subprocess",
        command_summary: Optional[str] = None,
    ) -> Optional[AggregatedPattern]:
        """Learn from failed subprocess runs; no-op on success."""
        if result.returncode == 0:
            return None
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        cmd_bits = command_summary or " ".join(str(a) for a in (result.args or ())[:8])
        body = f"exit {result.returncode} cmd={cmd_bits}"
        if stderr:
            body += f" stderr={stderr}"
        elif stdout:
            body += f" stdout={stdout}"
        return self.record(
            body,
            exception_type="CalledProcessError",
            source=source,
            extra={"returncode": result.returncode},
        )

    def ingest_log_stream(self, lines: Iterable[str], *, source: str = "log_scan") -> int:
        """Detect likely error lines and record them; returns number of records ingested."""
        trigger = re.compile(
            r"\b(error|exception|traceback|failed|failure|fatal|critical)\b",
            re.IGNORECASE,
        )
        tb_end = re.compile(
            r"^([\w.]+(?:Error|Exception|Warning|KeyboardInterrupt|Exit)):\s",
        )
        ingested = 0
        buf: List[str] = []
        in_tb = False
        for line in lines:
            stripped = line.rstrip("\n")
            if stripped.strip().startswith("Traceback (most recent call last):"):
                in_tb = True
                buf = [stripped]
                continue
            if in_tb:
                buf.append(stripped)
                if tb_end.match(stripped.strip()):
                    block = "\n".join(buf)
                    self.record(block, source=source)
                    ingested += 1
                    in_tb = False
                    buf = []
                continue
            if trigger.search(stripped):
                self.record(stripped, source=source)
                ingested += 1
        if in_tb and buf:
            block = "\n".join(buf)
            self.record(block, source=f"{source}_incomplete_tb")
            ingested += 1
        return ingested

    def iter_patterns(self) -> Iterator[AggregatedPattern]:
        patterns = self._read_patterns()
        for row in sorted(patterns.values(), key=lambda p: (-p.count, p.last_seen)):
            yield row

    def top_patterns(self, limit: int = 30, category: Optional[ErrorCategory] = None) -> List[AggregatedPattern]:
        rows = list(self.iter_patterns())
        if category:
            rows = [r for r in rows if r.category == category]
        rows.sort(key=lambda p: (-p.count, p.last_seen))
        return rows[:limit]

    def summary(self) -> Dict[str, Any]:
        patterns = self._read_patterns()
        by_cat: Dict[str, int] = {}
        total_events = 0
        for p in patterns.values():
            total_events += p.count
            key = p.category.value
            by_cat[key] = by_cat.get(key, 0) + p.count
        return {
            "schema_version": SCHEMA_VERSION,
            "distinct_patterns": len(patterns),
            "total_occurrences": total_events,
            "by_category": dict(sorted(by_cat.items(), key=lambda kv: (-kv[1], kv[0]))),
            "storage_path": str(self.store_path),
        }

    def lessons_for_prompt(self, limit: int = 15) -> List[str]:
        """Short bullet points suitable for injecting into agent/system prompts."""
        lines: List[str] = []
        for p in self.top_patterns(limit=limit):
            hint = f"[{p.category.value}] {p.normalized_text}"
            if p.count > 1:
                hint += f" (seen {p.count}x)"
            lines.append(hint)
        return lines

    def report_markdown(self, *, top_n: int = 25) -> str:
        summary = self.summary()
        lines = [
            "# Error learning report",
            "",
            f"- Distinct patterns: **{summary['distinct_patterns']}**",
            f"- Total recorded occurrences: **{summary['total_occurrences']}**",
            "",
            "## By category",
            "",
        ]
        for cat, n in summary["by_category"].items():
            lines.append(f"- `{cat}`: {n}")
        lines.extend(["", "## Top patterns", ""])
        for p in self.top_patterns(limit=top_n):
            src_top = sorted(p.sources.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
            src_txt = ", ".join(f"{k} ({v})" for k, v in src_top)
            lines.append(f"- **{p.count}x** `{p.category.value}` — {p.normalized_text}")
            lines.append(f"  - last: {p.last_seen} | sources: {src_txt or 'n/a'}")
        return "\n".join(lines) + "\n"

    def _add_sample(self, row: AggregatedPattern, sample: str) -> None:
        sample = sample.strip()
        if not sample:
            return
        if sample in row.samples:
            return
        row.samples.append(sample)
        if len(row.samples) > self.max_samples:
            row.samples = row.samples[-self.max_samples :]

    def _prune_if_needed(self, patterns: Dict[str, AggregatedPattern]) -> None:
        if len(patterns) <= self.max_patterns:
            return
        # Drop oldest least-recently-seen patterns first (by last_seen then count).
        sorted_rows = sorted(patterns.values(), key=lambda p: (p.last_seen, p.count))
        overflow = len(patterns) - self.max_patterns
        for row in sorted_rows[:overflow]:
            patterns.pop(row.signature, None)

    def _load_patterns_unlocked(self) -> Dict[str, AggregatedPattern]:
        if not self.store_path.exists():
            return {}
        try:
            raw = json.loads(self.store_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            corrupt = self.storage_dir / f"{STORE_FILENAME}.corrupt.{int(_utc_now().timestamp())}"
            try:
                self.store_path.rename(corrupt)
            except OSError:
                pass
            return {}
        if not isinstance(raw, dict):
            return {}
        version = raw.get("schema_version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            # Single-version store for now; reset on mismatch.
            backup = self.storage_dir / f"{STORE_FILENAME}.v{version}.bak"
            try:
                if self.store_path.exists():
                    self.store_path.rename(backup)
            except OSError:
                pass
            return {}
        patterns_raw = raw.get("patterns")
        if not isinstance(patterns_raw, list):
            return {}
        out: Dict[str, AggregatedPattern] = {}
        for item in patterns_raw:
            if isinstance(item, dict):
                row = AggregatedPattern.from_dict(item)
                if row.signature:
                    out[row.signature] = row
        return out

    def _save_patterns_unlocked(self, patterns: Dict[str, AggregatedPattern]) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "updated_at": self._clock().isoformat(),
            "patterns": [p.as_dict() for p in sorted(patterns.values(), key=lambda p: (-p.count, p.signature))],
        }
        text = json.dumps(payload, indent=2, sort_keys=True)
        _atomic_write_text(self.store_path, text)

    def _append_audit_event(self, event: Dict[str, Any]) -> None:
        day = self._clock().strftime("%Y%m%d")
        path = self.storage_dir / f"audit_{day}.jsonl"
        line = json.dumps(event, sort_keys=True)
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Error learning: capture and aggregate failures")
    p.add_argument(
        "command",
        choices=["report", "summary", "lessons", "ingest"],
        help="report: markdown; summary: JSON stats; lessons: prompt bullets; ingest: read stdin as log lines",
    )
    p.add_argument(
        "--storage-dir",
        default=None,
        help="Directory for error_patterns_v1.json (default: ./logs/error_learning)",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    learner = ErrorLearningSystem(storage_dir=args.storage_dir) if args.storage_dir else ErrorLearningSystem()

    if args.command == "report":
        sys.stdout.write(learner.report_markdown())
        return 0
    if args.command == "summary":
        print(json.dumps(learner.summary(), indent=2))
        return 0
    if args.command == "lessons":
        for line in learner.lessons_for_prompt():
            print(f"- {line}")
        return 0

    ingested = learner.ingest_log_stream(sys.stdin.read().splitlines(), source="cli_ingest")
    print(f"Ingested {ingested} error line(s).")
    print(json.dumps(learner.summary(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
