from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _default_memory_dir(root_dir: Path) -> Path:
    override = os.environ.get("OPENCLAW_MEMORY_DIR")
    if override:
        return Path(override).expanduser()
    return root_dir / "memory"


def _default_logs_dir(root_dir: Path) -> Path:
    override = os.environ.get("OPENCLAW_LOG_DIR")
    if override:
        return Path(override).expanduser()
    return root_dir / "logs"


class ReflectionInterval:
    """Supported reflection schedules."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"

    ALL = {HOURLY, DAILY, WEEKLY}

    @classmethod
    def parse(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in cls.ALL:
            raise ValueError(f"Unsupported interval '{value}'. Expected one of {sorted(cls.ALL)}.")
        return normalized


@dataclass(frozen=True)
class ReflectionConfig:
    """Configuration for automated self-reflection runs."""

    interval: str = ReflectionInterval.DAILY
    lookback_hours: int = 24
    max_recent_memory_files: int = 5
    max_recent_log_lines: int = 5000

    def __post_init__(self) -> None:
        ReflectionInterval.parse(self.interval)
        if self.lookback_hours <= 0:
            raise ValueError("lookback_hours must be positive.")
        if self.max_recent_memory_files <= 0:
            raise ValueError("max_recent_memory_files must be positive.")
        if self.max_recent_log_lines <= 0:
            raise ValueError("max_recent_log_lines must be positive.")

    @property
    def lookback(self) -> timedelta:
        return timedelta(hours=self.lookback_hours)


@dataclass
class ImprovementMetrics:
    """A snapshot of improvement metrics for a reflection period."""

    period_start: str
    period_end: str
    tasks_total: int = 0
    tasks_success: int = 0
    tasks_failed: int = 0
    tasks_skipped: int = 0
    error_events: int = 0
    warning_events: int = 0
    auto_improvement_actions: int = 0
    notes: Dict[str, Any] = field(default_factory=dict)

    @property
    def task_completion_rate(self) -> float | None:
        if self.tasks_total <= 0:
            return None
        return round((self.tasks_success / self.tasks_total) * 100.0, 1)

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "period_start": self.period_start,
            "period_end": self.period_end,
            "tasks_total": self.tasks_total,
            "tasks_success": self.tasks_success,
            "tasks_failed": self.tasks_failed,
            "tasks_skipped": self.tasks_skipped,
            "task_completion_rate": self.task_completion_rate,
            "error_events": self.error_events,
            "warning_events": self.warning_events,
            "auto_improvement_actions": self.auto_improvement_actions,
            "notes": self.notes,
        }
        return payload


@dataclass
class ReflectionReport:
    """Rendered reflection output."""

    generated_at: str
    period_start: str
    period_end: str
    interval: str
    what_went_well: List[str]
    could_improve: List[str]
    action_items: List[str]
    metrics: ImprovementMetrics
    sources: Dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        generated_day = self.generated_at.split("T", 1)[0]
        lines: list[str] = [
            f"# Self-Reflection ({generated_day})",
            "",
            f"- Generated at (UTC): {self.generated_at}",
            f"- Interval: `{self.interval}`",
            f"- Period: {self.period_start} → {self.period_end}",
            "",
            "## Improvement Metrics",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Tasks total | {self.metrics.tasks_total} |",
            f"| Tasks success | {self.metrics.tasks_success} |",
            f"| Tasks failed | {self.metrics.tasks_failed} |",
            f"| Tasks skipped | {self.metrics.tasks_skipped} |",
            f"| Task completion rate | {self.metrics.task_completion_rate if self.metrics.task_completion_rate is not None else 'n/a'} |",
            f"| Error frequency (events) | {self.metrics.error_events} |",
            f"| Warning frequency (events) | {self.metrics.warning_events} |",
            f"| Auto-improvement actions | {self.metrics.auto_improvement_actions} |",
            "",
            "## What Went Well",
            "",
        ]
        if self.what_went_well:
            lines.extend([f"- {item}" for item in self.what_went_well])
        else:
            lines.append("- (No strong positives detected in the current lookback window.)")

        lines.extend(["", "## What Could Be Improved", ""])
        if self.could_improve:
            lines.extend([f"- {item}" for item in self.could_improve])
        else:
            lines.append("- (No clear improvement opportunities detected in the current lookback window.)")

        lines.extend(["", "## Action Items For Next Period", ""])
        if self.action_items:
            lines.extend([f"- [ ] {item}" for item in self.action_items])
        else:
            lines.append("- [ ] (No action items generated; consider adding a manual goal.)")

        lines.extend(["", "## Sources", ""])
        if not self.sources:
            lines.append("- (No sources recorded.)")
        else:
            for key in sorted(self.sources):
                value = self.sources[key]
                if isinstance(value, list):
                    lines.append(f"- **{key}**: {len(value)} item(s)")
                else:
                    lines.append(f"- **{key}**: {value}")

        return "\n".join(lines) + "\n"


class AutoReflectionEngine:
    """Automated self-reflection engine.

    The engine is intentionally stdlib-only and reads from repository-local
    artifacts such as `logs/task_results.jsonl`, `logs/auto_improvements_*.json`,
    and recent `memory/*.md` files to generate a markdown reflection report.
    """

    ERROR_PATTERN = re.compile(
        r"\b(error|exception|traceback|failed|failure|timeout|timed out|"
        r"syntaxerror|valueerror|keyerror|runtimeerror|typeerror|importerror)\b",
        re.IGNORECASE,
    )
    WARNING_PATTERN = re.compile(r"\b(warn|warning)\b", re.IGNORECASE)

    def __init__(
        self,
        *,
        root_dir: Path | str | None = None,
        config: ReflectionConfig | None = None,
        memory_dir: Path | str | None = None,
        logs_dir: Path | str | None = None,
    ) -> None:
        self.root_dir = Path(root_dir or Path.cwd()).resolve()
        self.config = config or ReflectionConfig()
        self.memory_dir = Path(memory_dir) if memory_dir is not None else _default_memory_dir(self.root_dir)
        self.logs_dir = Path(logs_dir) if logs_dir is not None else _default_logs_dir(self.root_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def run_if_due(self, *, now: datetime | None = None) -> Path | None:
        """Generate and store a reflection report if the schedule is due."""

        now = now or _now_utc()
        state = self._load_state()
        last_run_raw = state.get("last_run_at")
        last_run = self._parse_datetime(last_run_raw) if isinstance(last_run_raw, str) else None
        if last_run is not None and not self.is_due(last_run, now=now):
            return None
        report_path = self.run(now=now)
        state["last_run_at"] = now.replace(microsecond=0).isoformat()
        state["last_interval"] = self.config.interval
        self._save_state(state)
        return report_path

    def run(self, *, now: datetime | None = None) -> Path:
        """Generate and store a reflection report immediately."""

        now = (now or _now_utc()).replace(microsecond=0)
        period_start = now - self.config.lookback
        sources: dict[str, Any] = {}

        task_metrics, task_insights, task_actions = self._analyze_task_results(period_start, now)
        sources.update(task_metrics.get("sources", {}))

        improvement_metrics, improvement_insights, improvement_actions = self._analyze_auto_improvements(
            period_start, now
        )
        sources.update(improvement_metrics.get("sources", {}))

        memory_insights, memory_actions, memory_sources = self._analyze_recent_memory(now=now)
        sources.update(memory_sources)

        metrics = ImprovementMetrics(
            period_start=period_start.isoformat(),
            period_end=now.isoformat(),
            tasks_total=task_metrics["tasks_total"],
            tasks_success=task_metrics["tasks_success"],
            tasks_failed=task_metrics["tasks_failed"],
            tasks_skipped=task_metrics["tasks_skipped"],
            error_events=task_metrics["error_events"] + improvement_metrics["error_events"],
            warning_events=task_metrics["warning_events"] + improvement_metrics["warning_events"],
            auto_improvement_actions=improvement_metrics["actions_total"],
            notes={
                "task_sources": task_metrics.get("sources", {}),
                "improvement_sources": improvement_metrics.get("sources", {}),
            },
        )

        what_went_well = self._dedupe_preserve_order(
            [
                *task_insights.get("went_well", []),
                *improvement_insights.get("went_well", []),
                *memory_insights.get("went_well", []),
            ]
        )
        could_improve = self._dedupe_preserve_order(
            [
                *task_insights.get("improve", []),
                *improvement_insights.get("improve", []),
                *memory_insights.get("improve", []),
            ]
        )
        action_items = self._dedupe_preserve_order(
            [
                *task_actions,
                *improvement_actions,
                *memory_actions,
            ]
        )

        report = ReflectionReport(
            generated_at=now.isoformat(),
            period_start=period_start.isoformat(),
            period_end=now.isoformat(),
            interval=self.config.interval,
            what_went_well=what_went_well,
            could_improve=could_improve,
            action_items=action_items,
            metrics=metrics,
            sources=sources,
        )

        report_path = self._report_path_for_day(now.date())
        report_path.write_text(report.to_markdown(), encoding="utf-8")
        self._append_metrics(metrics)
        return report_path

    def is_due(self, last_run_at: datetime, *, now: datetime | None = None) -> bool:
        now = now or _now_utc()
        interval = ReflectionInterval.parse(self.config.interval)
        if interval == ReflectionInterval.HOURLY:
            return now - last_run_at >= timedelta(hours=1)
        if interval == ReflectionInterval.DAILY:
            return now.date() != last_run_at.date()
        if interval == ReflectionInterval.WEEKLY:
            # Trigger when we cross into a new ISO week.
            return now.isocalendar()[:2] != last_run_at.isocalendar()[:2]
        return True

    def next_due_at(self, last_run_at: datetime) -> datetime:
        interval = ReflectionInterval.parse(self.config.interval)
        if interval == ReflectionInterval.HOURLY:
            return last_run_at + timedelta(hours=1)
        if interval == ReflectionInterval.DAILY:
            return datetime.combine(last_run_at.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        # Weekly: next Monday 00:00 UTC after last run's ISO week.
        iso_year, iso_week, _ = last_run_at.isocalendar()
        # Get Monday of current ISO week.
        monday = date.fromisocalendar(iso_year, iso_week, 1)
        next_monday = monday + timedelta(days=7)
        return datetime.combine(next_monday, datetime.min.time(), tzinfo=timezone.utc)

    def _report_path_for_day(self, day: date) -> Path:
        return self.memory_dir / f"{day.strftime('%Y-%m-%d')}-reflection.md"

    def _state_path(self) -> Path:
        return self.memory_dir / "reflection_state.json"

    def _metrics_path(self) -> Path:
        return self.memory_dir / "reflection_metrics.json"

    def _load_state(self) -> Dict[str, Any]:
        path = self._state_path()
        if not path.exists():
            return {"version": 1, "last_run_at": None}
        payload = _safe_json_loads(_safe_read_text(path))
        return payload if isinstance(payload, dict) else {"version": 1, "last_run_at": None}

    def _save_state(self, state: Dict[str, Any]) -> None:
        path = self._state_path()
        path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _load_metrics_history(self) -> List[Dict[str, Any]]:
        path = self._metrics_path()
        if not path.exists():
            return []
        payload = _safe_json_loads(_safe_read_text(path))
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def _append_metrics(self, metrics: ImprovementMetrics) -> None:
        history = self._load_metrics_history()
        history.append(metrics.as_dict())
        # Keep the file bounded so it does not grow without limit.
        if len(history) > 365:
            history = history[-365:]
        self._metrics_path().write_text(json.dumps(history, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _parse_datetime(self, raw: str) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _analyze_task_results(
        self, period_start: datetime, period_end: datetime
    ) -> Tuple[Dict[str, Any], Dict[str, List[str]], List[str]]:
        log_path = self.logs_dir / "task_results.jsonl"
        sources: dict[str, Any] = {"task_results_log": str(log_path) if log_path.exists() else None}
        counts = {"pending": 0, "running": 0, "success": 0, "failed": 0, "skipped": 0}
        error_events = 0
        warning_events = 0
        slow_tasks: list[tuple[str, float]] = []
        recent_failures: list[str] = []

        if not log_path.exists():
            metrics = {
                "tasks_total": 0,
                "tasks_success": 0,
                "tasks_failed": 0,
                "tasks_skipped": 0,
                "error_events": 0,
                "warning_events": 0,
                "sources": sources,
            }
            insights = {
                "went_well": [],
                "improve": ["No task history found (`logs/task_results.jsonl` missing)."],
            }
            actions = ["Add a task runner log (`logs/task_results.jsonl`) to enable completion-rate tracking."]
            return metrics, insights, actions

        lines = _safe_read_text(log_path).splitlines()
        if len(lines) > self.config.max_recent_log_lines:
            lines = lines[-self.config.max_recent_log_lines :]

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            record = _safe_json_loads(line)
            if not isinstance(record, dict):
                continue
            ended_at = record.get("ended_at")
            ended_dt = self._parse_datetime(ended_at) if isinstance(ended_at, str) else None
            if ended_dt is None:
                continue
            if ended_dt < period_start or ended_dt > period_end:
                continue

            status = str(record.get("status", "")).lower()
            if status in counts:
                counts[status] += 1
            else:
                continue

            name = str(record.get("name", "unknown-task"))
            error = record.get("error")
            if status == "failed":
                recent_failures.append(f"{name}: {str(error or 'unknown failure')[:180]}")
                error_events += 1
            else:
                text_blob = json.dumps(record, sort_keys=True)
                if self.ERROR_PATTERN.search(text_blob):
                    error_events += 1
                if self.WARNING_PATTERN.search(text_blob):
                    warning_events += 1

            duration = record.get("duration_seconds")
            if isinstance(duration, (int, float)) and float(duration) >= 60:
                slow_tasks.append((name, float(duration)))

        tasks_total = counts["success"] + counts["failed"] + counts["skipped"]
        metrics = {
            "tasks_total": tasks_total,
            "tasks_success": counts["success"],
            "tasks_failed": counts["failed"],
            "tasks_skipped": counts["skipped"],
            "error_events": error_events,
            "warning_events": warning_events,
            "sources": sources,
        }

        went_well: list[str] = []
        improve: list[str] = []
        actions: list[str] = []

        completion_rate = None if tasks_total == 0 else round((counts["success"] / tasks_total) * 100.0, 1)
        if completion_rate is not None and completion_rate >= 80:
            went_well.append(f"Strong execution: task completion rate {completion_rate}%.")
        elif completion_rate is not None and completion_rate < 50 and tasks_total >= 4:
            improve.append(f"Low task completion rate ({completion_rate}%) suggests too much scope or flaky workflows.")
            actions.append("Reduce task scope or add retries/guardrails for the highest-failure workflows.")

        if counts["failed"] > 0:
            sample = "; ".join(recent_failures[:3])
            improve.append(f"Task failures detected ({counts['failed']}): {sample}.")
            actions.append("Pick the top failing task and add a regression test or clearer error handling.")
        elif tasks_total > 0:
            went_well.append("No task failures recorded in this period.")

        if slow_tasks:
            slow_tasks.sort(key=lambda item: item[1], reverse=True)
            worst = ", ".join(f"{name} ({seconds:.1f}s)" for name, seconds in slow_tasks[:3])
            improve.append(f"Slow task executions observed: {worst}.")
            actions.append("Profile the slowest task and add caching or tighter timeouts where safe.")

        if tasks_total == 0:
            improve.append("No completed tasks were logged in this period.")
            actions.append("Ensure task runs are captured by `TaskRunner` so progress can be measured.")

        return metrics, {"went_well": went_well, "improve": improve}, actions

    def _analyze_auto_improvements(
        self, period_start: datetime, period_end: datetime
    ) -> Tuple[Dict[str, Any], Dict[str, List[str]], List[str]]:
        sources: dict[str, Any] = {}
        entries: list[dict[str, Any]] = []
        action_count = 0
        warning_count = 0
        error_count = 0

        for path in sorted(self.logs_dir.glob("auto_improvements_*.json")):
            sources.setdefault("auto_improvement_logs", []).append(str(path))
            payload = _safe_json_loads(_safe_read_text(path))
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                timestamp = item.get("timestamp")
                dt = self._parse_datetime(timestamp) if isinstance(timestamp, str) else None
                if dt is None or dt < period_start or dt > period_end:
                    continue
                entries.append(item)

        for entry in entries:
            category = str(entry.get("category", "")).lower()
            outcome = str(entry.get("outcome", "")).lower()
            action_count += 1
            if category == "warning" or outcome in {"failed"}:
                warning_count += 1
            text_blob = json.dumps(entry, sort_keys=True)
            if self.ERROR_PATTERN.search(text_blob):
                error_count += 1

        went_well: list[str] = []
        improve: list[str] = []
        actions: list[str] = []

        if action_count == 0:
            improve.append("No auto-improvement actions were logged in this period.")
            actions.append("Run the auto-improvement engine periodically to detect health issues early.")
        else:
            went_well.append(f"Auto-improvement engine recorded {action_count} action(s) this period.")
            if warning_count > 0:
                improve.append(f"Auto-improvement warnings/failures recorded: {warning_count}.")
                actions.append("Review recent auto-improvement warnings and fix the underlying root cause.")

        metrics = {
            "actions_total": action_count,
            "warning_events": warning_count,
            "error_events": error_count,
            "sources": sources,
        }
        return metrics, {"went_well": went_well, "improve": improve}, actions

    def _analyze_recent_memory(self, *, now: datetime) -> Tuple[Dict[str, List[str]], List[str], Dict[str, Any]]:
        sources: dict[str, Any] = {}
        went_well: list[str] = []
        improve: list[str] = []
        actions: list[str] = []

        memory_files = [path for path in self.memory_dir.glob("*.md") if path.is_file()]
        memory_files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        memory_files = memory_files[: self.config.max_recent_memory_files]
        sources["memory_files_considered"] = [str(path) for path in memory_files]

        if not memory_files:
            improve.append("No recent `memory/*.md` notes found to summarize conversations/actions.")
            actions.append("Capture key decisions and outcomes in `memory/` so reflections can reference them.")
            return {"went_well": went_well, "improve": improve}, actions, sources

        keywords_done = re.compile(r"\b(done|shipped|merged|fixed|resolved|completed)\b", re.IGNORECASE)
        keywords_blocked = re.compile(r"\b(blocked|stuck|investigate|debug|todo|fixme)\b", re.IGNORECASE)

        done_hits = 0
        blocked_hits = 0
        for path in memory_files:
            text = _safe_read_text(path)
            done_hits += len(keywords_done.findall(text))
            blocked_hits += len(keywords_blocked.findall(text))

        if done_hits >= blocked_hits and done_hits > 0:
            went_well.append("Recent notes show steady progress (more completion signals than blockers).")
        if blocked_hits > done_hits and blocked_hits > 0:
            improve.append("Recent notes contain more blocker/todo signals than completion signals.")
            actions.append("Convert the top blocker into a concrete task with clear exit criteria.")

        # Encourage review of the newest memory file.
        newest = memory_files[0]
        newest_day = datetime.fromtimestamp(newest.stat().st_mtime, tz=timezone.utc).date()
        if (now.date() - newest_day).days >= 2:
            improve.append("Memory notes look stale (latest note is older than 2 days).")
            actions.append("Add a short daily memory note capturing decisions, outcomes, and follow-ups.")

        return {"went_well": went_well, "improve": improve}, actions, sources

    @staticmethod
    def _dedupe_preserve_order(items: Sequence[str]) -> List[str]:
        seen: set[str] = set()
        output: list[str] = []
        for item in items:
            cleaned = re.sub(r"\s+", " ", str(item).strip())
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            output.append(cleaned)
        return output


__all__ = [
    "AutoReflectionEngine",
    "ImprovementMetrics",
    "ReflectionConfig",
    "ReflectionInterval",
    "ReflectionReport",
]

