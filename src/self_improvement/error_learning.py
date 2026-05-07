from __future__ import annotations

import hashlib
import json
import traceback
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_json(obj: Any) -> Any:
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        return repr(obj)


def _normalize_message(message: str) -> str:
    normalized = " ".join((message or "").strip().split())
    return normalized[:500]


def _hash_signature(parts: Sequence[str]) -> str:
    payload = "\n".join(parts).encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:12]


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _read_json_path(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _write_json_path(path: Path, payload: Any) -> None:
    _ensure_parent_dir(path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


class ErrorCategory:
    RECOVERABLE = "recoverable"
    RECOVERABLE_WITH_FIX = "recoverable_with_fix"
    FATAL = "fatal"


def default_error_classifier(exc: BaseException) -> str:
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return ErrorCategory.FATAL
    if isinstance(exc, (ImportError, ModuleNotFoundError, AttributeError, SyntaxError)):
        return ErrorCategory.RECOVERABLE_WITH_FIX
    if isinstance(exc, (FileNotFoundError, TimeoutError, ConnectionError, ValueError, OSError)):
        return ErrorCategory.RECOVERABLE
    return ErrorCategory.RECOVERABLE


@dataclass(frozen=True)
class ErrorContext:
    module: str
    user_action: str
    details: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "module": self.module,
            "user_action": self.user_action,
            "details": {k: _safe_json(v) for k, v in (self.details or {}).items()},
        }


@dataclass(frozen=True)
class ErrorEvent:
    timestamp: str
    error_type: str
    category: str
    module: str
    user_action: str
    message: str
    stack_trace: str
    signature: str
    context: Dict[str, Any] = field(default_factory=dict)
    suggested_fix: Optional[str] = None
    solution: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FixRecord:
    signature: str
    error_type: str
    module: str
    normalized_message: str
    occurrences: int = 0
    last_seen: Optional[str] = None
    suggested_fix: Optional[str] = None
    solution: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ErrorLearningEngine:
    """
    Error learning and self-improvement module.

    Responsibilities:
    - Log errors with rich context to daily JSON files.
    - Classify errors into recoverability categories.
    - Track repeating error patterns and suggest fixes based on historical data.
    - Append LEARNINGS.md entries under .learnings/ when a solution is provided.
    - Export error statistics for analytics (count by type/module/time period).
    """

    def __init__(
        self,
        root_dir: Path | str | None = None,
        *,
        log_dir: Path | str | None = None,
        learnings_dir: Path | str | None = None,
        classifier: Optional[Callable[[BaseException], str]] = None,
    ) -> None:
        self.root_dir = Path(root_dir or Path.cwd())
        self.log_dir = Path(log_dir or self.root_dir / "logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.learnings_dir = Path(learnings_dir or self.root_dir / ".learnings")
        self.learnings_dir.mkdir(parents=True, exist_ok=True)
        self.classifier = classifier or default_error_classifier

    def _error_log_path_for_day(self, day: Optional[date] = None) -> Path:
        target_day = day or _now_utc().date()
        return self.log_dir / f"errors_{target_day.strftime('%Y%m%d')}.json"

    def _fix_index_path(self) -> Path:
        return self.learnings_dir / "error_fixes.json"

    def _learnings_md_path(self) -> Path:
        return self.learnings_dir / "LEARNINGS.md"

    def _compute_signature(self, *, error_type: str, module: str, message: str) -> str:
        normalized = _normalize_message(message)
        return _hash_signature([error_type, module, normalized])

    def log_error(
        self,
        exc: BaseException,
        *,
        context: ErrorContext,
        suggested_fix: Optional[str] = None,
        solution: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> ErrorEvent:
        ts = (timestamp or _now_utc()).isoformat()
        error_type = type(exc).__name__
        message = _normalize_message(str(exc))
        stack_trace = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ).strip()
        category = self.classifier(exc)
        signature = self._compute_signature(error_type=error_type, module=context.module, message=message)

        event = ErrorEvent(
            timestamp=ts,
            error_type=error_type,
            category=category,
            module=context.module,
            user_action=context.user_action,
            message=message,
            stack_trace=stack_trace,
            signature=signature,
            context=context.as_dict(),
            suggested_fix=suggested_fix,
            solution=solution,
        )

        self._append_error_event(event)
        self._update_fix_index(event)
        if solution:
            self.append_learning_entry(event, solution=solution, suggested_fix=suggested_fix)
        return event

    def _append_error_event(self, event: ErrorEvent) -> None:
        path = self._error_log_path_for_day()
        entries: List[Dict[str, Any]] = []
        if path.exists():
            payload = _read_json_path(path, default=[])
            if isinstance(payload, list):
                entries = [item for item in payload if isinstance(item, dict)]
        entries.append(event.as_dict())
        _write_json_path(path, entries)

    def _update_fix_index(self, event: ErrorEvent) -> None:
        idx_path = self._fix_index_path()
        payload = _read_json_path(idx_path, default={})
        if not isinstance(payload, dict):
            payload = {}
        sig = event.signature
        record_raw = payload.get(sig, {})
        if not isinstance(record_raw, dict):
            record_raw = {}

        record = FixRecord(
            signature=sig,
            error_type=event.error_type,
            module=event.module,
            normalized_message=_normalize_message(event.message),
            occurrences=int(record_raw.get("occurrences", 0) or 0) + 1,
            last_seen=event.timestamp,
            suggested_fix=event.suggested_fix or record_raw.get("suggested_fix"),
            solution=event.solution or record_raw.get("solution"),
        )
        payload[sig] = record.as_dict()
        _write_json_path(idx_path, payload)

    def suggest_fix(
        self,
        *,
        error_type: str,
        module: str,
        message: str,
    ) -> Optional[str]:
        signature = self._compute_signature(error_type=error_type, module=module, message=message)
        payload = _read_json_path(self._fix_index_path(), default={})
        if not isinstance(payload, dict):
            return None
        raw = payload.get(signature)
        if not isinstance(raw, dict):
            return None
        fix = raw.get("suggested_fix") or raw.get("solution")
        return str(fix) if fix else None

    def iter_error_events(self, *, since_days: Optional[int] = None) -> Iterable[Dict[str, Any]]:
        cutoff: Optional[date] = None
        if since_days is not None:
            cutoff = (_now_utc() - timedelta(days=since_days)).date()

        for path in sorted(self.log_dir.glob("errors_*.json")):
            stem = path.stem.rsplit("_", 1)[-1]
            try:
                file_day = datetime.strptime(stem, "%Y%m%d").date()
            except ValueError:
                continue
            if cutoff and file_day < cutoff:
                continue
            payload = _read_json_path(path, default=[])
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        yield item

    def append_learning_entry(
        self,
        event: ErrorEvent,
        *,
        solution: str,
        suggested_fix: Optional[str] = None,
    ) -> Path:
        path = self._learnings_md_path()
        if not path.exists():
            _ensure_parent_dir(path)
            path.write_text("# Learnings\n\n", encoding="utf-8")

        lines: List[str] = []
        day = event.timestamp.split("T", 1)[0]
        title = f"## {day} - {event.error_type} in {event.module}"
        lines.append(title)
        lines.append("")
        lines.append(f"- **Signature**: `{event.signature}`")
        lines.append(f"- **Category**: `{event.category}`")
        lines.append(f"- **User action**: {event.user_action}")
        lines.append(f"- **Timestamp**: `{event.timestamp}`")
        if suggested_fix:
            lines.append(f"- **Suggested fix**: {suggested_fix}")
        lines.append("")
        lines.append("### Context")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(event.context, indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
        lines.append("### Stack trace")
        lines.append("")
        lines.append("```text")
        lines.append(event.stack_trace.strip() or "(no stack trace)")
        lines.append("```")
        lines.append("")
        lines.append("### Solution")
        lines.append("")
        lines.append(solution.strip())
        lines.append("")

        existing = path.read_text(encoding="utf-8")
        if f"`{event.signature}`" in existing:
            return path

        path.write_text(existing + "\n".join(lines), encoding="utf-8")
        return path

    def export_statistics(
        self,
        *,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        period: str = "day",
    ) -> Dict[str, Any]:
        """
        Export error statistics.

        Returns:
            Dict with:
              - counts_by_type
              - counts_by_module
              - counts_by_category
              - counts_by_period (time series)
        """

        def parse_ts(raw: Any) -> Optional[datetime]:
            if not raw:
                return None
            if isinstance(raw, datetime):
                return raw
            if isinstance(raw, str):
                try:
                    return datetime.fromisoformat(raw)
                except ValueError:
                    return None
            return None

        def period_key(dt: datetime) -> str:
            if period == "hour":
                return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:00Z")
            if period == "week":
                iso = dt.astimezone(timezone.utc).isocalendar()
                return f"{iso.year}-W{iso.week:02d}"
            if period == "month":
                return dt.astimezone(timezone.utc).strftime("%Y-%m")
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")

        since_dt = since.astimezone(timezone.utc) if since else None
        until_dt = until.astimezone(timezone.utc) if until else None

        counts_by_type: Dict[str, int] = {}
        counts_by_module: Dict[str, int] = {}
        counts_by_category: Dict[str, int] = {}
        counts_by_period: Dict[str, int] = {}

        for item in self.iter_error_events(since_days=None):
            ts = parse_ts(item.get("timestamp"))
            if ts is None:
                continue
            ts_utc = ts.astimezone(timezone.utc)
            if since_dt and ts_utc < since_dt:
                continue
            if until_dt and ts_utc > until_dt:
                continue

            error_type = str(item.get("error_type", "unknown"))
            module = str(item.get("module", "unknown"))
            category = str(item.get("category", "unknown"))
            pk = period_key(ts_utc)

            counts_by_type[error_type] = counts_by_type.get(error_type, 0) + 1
            counts_by_module[module] = counts_by_module.get(module, 0) + 1
            counts_by_category[category] = counts_by_category.get(category, 0) + 1
            counts_by_period[pk] = counts_by_period.get(pk, 0) + 1

        return {
            "counts_by_type": dict(sorted(counts_by_type.items())),
            "counts_by_module": dict(sorted(counts_by_module.items())),
            "counts_by_category": dict(sorted(counts_by_category.items())),
            "counts_by_period": dict(sorted(counts_by_period.items())),
            "period": period,
            "since": since_dt.isoformat() if since_dt else None,
            "until": until_dt.isoformat() if until_dt else None,
        }

