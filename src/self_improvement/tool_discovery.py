"""Tool discovery and recommendation utilities.

This module records tool usage events, builds aggregate statistics, and produces
markdown recommendations intended for dashboards and self-improvement loops.

Key features:
- Track tool/skill usage frequency and success rates.
- Build a tool x task-type success matrix.
- Recommend tools for a given task context using historical outcomes.
- Identify underutilized tools that may be relevant.
- Persist daily event logs and a summarized dashboard JSON snapshot.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _default_openclaw_home() -> Path:
    override = os.environ.get("OPENCLAW_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".openclaw"


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_task_type(task_type: str) -> str:
    return "unknown" if not task_type else str(task_type).strip().lower()


def _normalize_tool(tool: str) -> str:
    return "unknown" if not tool else str(tool).strip()


def _tokenize(text: str) -> List[str]:
    tokens = []
    for raw in (text or "").replace("/", " ").replace("_", " ").replace("-", " ").split():
        cleaned = "".join(ch for ch in raw.lower() if ch.isalnum())
        if cleaned:
            tokens.append(cleaned)
    return tokens


@dataclass(frozen=True)
class ToolCatalogEntry:
    """Optional metadata used to improve recommendations."""

    name: str
    description: str = ""
    tags: Tuple[str, ...] = ()
    task_types: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "task_types": list(self.task_types),
        }


@dataclass
class ToolUsageEvent:
    """A single tool usage observation."""

    timestamp: str
    tool: str
    task_type: str
    success: bool
    skills: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    duration_seconds: Optional[float] = None
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "tool": self.tool,
            "task_type": self.task_type,
            "success": self.success,
            "skills": list(self.skills),
            "context": self.context,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
        }


@dataclass
class ToolRecommendation:
    tool: str
    score: float
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return {"tool": self.tool, "score": self.score, "reason": self.reason}


@dataclass
class ToolDiscoverySnapshot:
    """Aggregated usage statistics suitable for dashboards."""

    generated_at: str
    window_days: Optional[int]
    tool_usage_counts: Dict[str, int]
    skill_usage_counts: Dict[str, int]
    tool_success_rates: Dict[str, Dict[str, Any]]
    usage_matrix: Dict[str, Dict[str, Dict[str, Any]]]
    underutilized_tools: List[Dict[str, Any]]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "window_days": self.window_days,
            "tool_usage_counts": self.tool_usage_counts,
            "skill_usage_counts": self.skill_usage_counts,
            "tool_success_rates": self.tool_success_rates,
            "usage_matrix": self.usage_matrix,
            "underutilized_tools": self.underutilized_tools,
        }


class ToolDiscoveryEngine:
    """Record tool usage and generate recommendations from history."""

    def __init__(
        self,
        *,
        log_dir: Path | str | None = None,
        tool_catalog: Optional[Mapping[str, ToolCatalogEntry]] = None,
    ) -> None:
        default_dir = _default_openclaw_home() / "logs"
        self.log_dir = Path(log_dir or default_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.tool_catalog: Dict[str, ToolCatalogEntry] = dict(tool_catalog or {})

    def record_usage(
        self,
        *,
        tool: str,
        task_type: str,
        success: bool,
        skills: Optional[Sequence[str]] = None,
        context: Optional[Mapping[str, Any]] = None,
        duration_seconds: Optional[float] = None,
        error: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> ToolUsageEvent:
        """Append a tool usage event to the daily usage log."""

        dt = timestamp or _now_utc()
        event = ToolUsageEvent(
            timestamp=dt.isoformat(),
            tool=_normalize_tool(tool),
            task_type=_normalize_task_type(task_type),
            success=bool(success),
            skills=[str(item).strip() for item in (skills or []) if str(item).strip()],
            context=dict(context or {}),
            duration_seconds=_safe_float(duration_seconds),
            error=(str(error).strip() or None) if error is not None else None,
        )
        self._append_event(event)
        return event

    def iter_events(self, *, since_days: Optional[int] = None) -> Iterable[Dict[str, Any]]:
        """Yield usage events as dicts from JSON log files."""

        cutoff: Optional[date] = None
        if since_days is not None:
            cutoff = (_now_utc() - timedelta(days=since_days)).date()

        for path in sorted(self.log_dir.glob("tool_usage_*.json")):
            stem = path.stem.rsplit("_", 1)[-1]
            try:
                file_day = datetime.strptime(stem, "%Y%m%d").date()
            except ValueError:
                continue
            if cutoff and file_day < cutoff:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        yield item

    def analyze_usage(
        self,
        *,
        since_days: Optional[int] = 30,
        underutilized_threshold: int = 1,
    ) -> ToolDiscoverySnapshot:
        """Aggregate usage statistics for a time window."""

        events = list(self.iter_events(since_days=since_days))
        tool_counts: Dict[str, int] = {}
        skill_counts: Dict[str, int] = {}
        tool_success: Dict[str, Dict[str, int]] = {}
        matrix: Dict[str, Dict[str, Dict[str, int]]] = {}

        for event in events:
            tool = _normalize_tool(str(event.get("tool", "unknown")))
            task_type = _normalize_task_type(str(event.get("task_type", "unknown")))
            success = bool(event.get("success", False))
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
            bucket = tool_success.setdefault(tool, {"success": 0, "failure": 0})
            bucket["success" if success else "failure"] += 1

            matrix.setdefault(tool, {}).setdefault(task_type, {"success": 0, "failure": 0})[
                "success" if success else "failure"
            ] += 1

            skills = event.get("skills", [])
            if isinstance(skills, list):
                for skill in skills:
                    name = str(skill).strip()
                    if not name:
                        continue
                    skill_counts[name] = skill_counts.get(name, 0) + 1

        tool_success_rates: Dict[str, Dict[str, Any]] = {}
        for tool, counts in tool_success.items():
            total = int(counts.get("success", 0) + counts.get("failure", 0))
            rate = (counts.get("success", 0) / total) if total else None
            tool_success_rates[tool] = {
                "success": int(counts.get("success", 0)),
                "failure": int(counts.get("failure", 0)),
                "total": total,
                "success_rate": round(rate, 4) if rate is not None else None,
            }

        matrix_payload: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for tool, task_buckets in matrix.items():
            matrix_payload[tool] = {}
            for task_type, counts in task_buckets.items():
                total = int(counts.get("success", 0) + counts.get("failure", 0))
                rate = (counts.get("success", 0) / total) if total else None
                matrix_payload[tool][task_type] = {
                    "success": int(counts.get("success", 0)),
                    "failure": int(counts.get("failure", 0)),
                    "total": total,
                    "success_rate": round(rate, 4) if rate is not None else None,
                }

        underutilized = self.identify_underutilized_tools(
            tool_counts=tool_counts,
            threshold=underutilized_threshold,
        )

        return ToolDiscoverySnapshot(
            generated_at=_now_utc().isoformat(),
            window_days=since_days,
            tool_usage_counts=dict(sorted(tool_counts.items(), key=lambda item: (-item[1], item[0].lower()))),
            skill_usage_counts=dict(sorted(skill_counts.items(), key=lambda item: (-item[1], item[0].lower()))),
            tool_success_rates=dict(sorted(tool_success_rates.items(), key=lambda item: item[0].lower())),
            usage_matrix=dict(sorted(matrix_payload.items(), key=lambda item: item[0].lower())),
            underutilized_tools=underutilized,
        )

    def identify_underutilized_tools(
        self,
        *,
        tool_counts: Optional[Mapping[str, int]] = None,
        threshold: int = 1,
        task_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return a list of tools with low usage, optionally filtered by task type."""

        counts = dict(tool_counts or {})
        normalized_task = _normalize_task_type(task_type) if task_type else None
        candidates: List[Tuple[str, int, ToolCatalogEntry | None]] = []
        for name, entry in sorted(self.tool_catalog.items(), key=lambda item: item[0].lower()):
            used = int(counts.get(name, 0))
            if used > threshold:
                continue
            if normalized_task and entry.task_types and normalized_task not in {t.lower() for t in entry.task_types}:
                continue
            candidates.append((name, used, entry))
        return [
            {
                "tool": tool,
                "usage_count": used,
                "description": (entry.description if entry else ""),
                "tags": list(entry.tags) if entry else [],
                "task_types": list(entry.task_types) if entry else [],
            }
            for tool, used, entry in candidates
        ]

    def recommend_tools(
        self,
        *,
        task_type: str,
        context_text: str = "",
        since_days: Optional[int] = 90,
        top_n: int = 5,
    ) -> List[ToolRecommendation]:
        """Recommend tools given a task type and free-form context text."""

        snapshot = self.analyze_usage(since_days=since_days)
        normalized_task = _normalize_task_type(task_type)
        context_tokens = set(_tokenize(context_text))

        recommendations: List[ToolRecommendation] = []
        for tool_name in self._candidate_tools_for_task(normalized_task):
            tool_counts = snapshot.tool_usage_counts.get(tool_name, 0)
            per_tool = snapshot.tool_success_rates.get(tool_name, {})
            overall_rate = per_tool.get("success_rate")
            matrix_cell = snapshot.usage_matrix.get(tool_name, {}).get(normalized_task, {})
            per_task_rate = matrix_cell.get("success_rate")

            catalog = self.tool_catalog.get(tool_name)
            tag_overlap = 0
            if catalog is not None and context_tokens:
                tag_overlap = len(context_tokens.intersection({t.lower() for t in catalog.tags}))

            # Score components:
            # - Prefer strong historical success on this task type.
            # - Prefer tools used enough times to be reliable (log-scaled).
            # - If we have no per-task rate, fall back to overall success rate.
            base_rate = per_task_rate if per_task_rate is not None else overall_rate
            rate_score = float(base_rate) if base_rate is not None else 0.5
            usage_weight = math.log(1 + tool_counts, 2)
            tag_bonus = 0.15 * tag_overlap
            score = (rate_score * (1.0 + 0.25 * usage_weight)) + tag_bonus

            reasons = []
            if per_task_rate is not None:
                reasons.append(f"task success rate {per_task_rate:.0%}")
            elif overall_rate is not None:
                reasons.append(f"overall success rate {overall_rate:.0%}")
            if tool_counts:
                reasons.append(f"{tool_counts} recent uses")
            if tag_overlap:
                reasons.append("context tag match")
            if not reasons:
                reasons.append("no prior history; catalog match")

            recommendations.append(
                ToolRecommendation(
                    tool=tool_name,
                    score=round(score, 4),
                    reason=", ".join(reasons),
                )
            )

        recommendations.sort(key=lambda item: (-item.score, item.tool.lower()))
        return recommendations[: max(1, int(top_n))]

    def build_usage_matrix(
        self,
        *,
        since_days: Optional[int] = 30,
    ) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Build and return the tool x task type matrix."""

        return self.analyze_usage(since_days=since_days).usage_matrix

    def generate_recommendations_markdown(
        self,
        *,
        task_type: str,
        context_text: str = "",
        since_days: Optional[int] = 90,
        top_n: int = 5,
        matrix_days: Optional[int] = 30,
        include_matrix: bool = True,
    ) -> str:
        """Generate a markdown report with tool recommendations and usage stats."""

        normalized_task = _normalize_task_type(task_type)
        snapshot = self.analyze_usage(since_days=matrix_days)
        recommendations = self.recommend_tools(
            task_type=normalized_task,
            context_text=context_text,
            since_days=since_days,
            top_n=top_n,
        )
        underutilized = self.identify_underutilized_tools(
            tool_counts=snapshot.tool_usage_counts,
            threshold=1,
            task_type=normalized_task,
        )

        lines = [
            f"# Tool Recommendations ({normalized_task})",
            "",
            f"Generated: {snapshot.generated_at}",
            "",
            "## Recommended tools",
        ]
        if not recommendations:
            lines.append("- No recommendations available yet.")
        else:
            for item in recommendations:
                desc = self.tool_catalog.get(item.tool).description if item.tool in self.tool_catalog else ""
                suffix = f" — {desc}" if desc else ""
                lines.append(f"- **{item.tool}** (score={item.score}): {item.reason}{suffix}")

        lines.extend(["", "## Underutilized tools to consider"])
        if not underutilized:
            lines.append("- None detected (or no catalog configured).")
        else:
            for entry in underutilized[:10]:
                desc = f" — {entry.get('description', '')}" if entry.get("description") else ""
                lines.append(f"- **{entry['tool']}** (uses={entry['usage_count']}): low usage{desc}")

        lines.extend(["", "## Most used tools"])
        if not snapshot.tool_usage_counts:
            lines.append("- No tool usage events recorded.")
        else:
            for tool, count in list(snapshot.tool_usage_counts.items())[:10]:
                rate = snapshot.tool_success_rates.get(tool, {}).get("success_rate")
                rate_text = f"{rate:.0%}" if isinstance(rate, (int, float)) else "n/a"
                lines.append(f"- **{tool}**: uses={count}, success={rate_text}")

        lines.extend(["", "## Most used skills"])
        if not snapshot.skill_usage_counts:
            lines.append("- No skills recorded yet.")
        else:
            for skill, count in list(snapshot.skill_usage_counts.items())[:10]:
                lines.append(f"- **{skill}**: {count}")

        if include_matrix:
            lines.extend(["", "## Tool usage matrix (success rate)"])
            lines.append("")
            lines.extend(self._render_matrix_table(snapshot.usage_matrix))

        return "\n".join(lines) + "\n"

    def write_dashboard_snapshot(
        self,
        *,
        since_days: Optional[int] = 30,
        path: Path | str | None = None,
    ) -> Path:
        """Write an aggregated JSON snapshot intended for dashboard display."""

        snapshot = self.analyze_usage(since_days=since_days)
        target = Path(path or (self.log_dir / "tool_usage_dashboard.json"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(snapshot.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target

    def _append_event(self, event: ToolUsageEvent) -> None:
        path = self._log_path_for_day()
        entries: List[Dict[str, Any]] = []
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, list):
                    entries = [item for item in payload if isinstance(item, dict)]
            except json.JSONDecodeError:
                entries = []
        entries.append(event.as_dict())
        path.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _log_path_for_day(self, day: Optional[date] = None) -> Path:
        target_day = day or _now_utc().date()
        return self.log_dir / f"tool_usage_{target_day.strftime('%Y%m%d')}.json"

    def _candidate_tools_for_task(self, task_type: str) -> List[str]:
        task_type = _normalize_task_type(task_type)
        if not self.tool_catalog:
            return []
        matches = []
        for tool, entry in self.tool_catalog.items():
            if entry.task_types:
                if task_type in {t.lower() for t in entry.task_types}:
                    matches.append(tool)
            else:
                matches.append(tool)
        return sorted(set(matches), key=lambda name: name.lower())

    def _render_matrix_table(self, matrix: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> List[str]:
        all_task_types = sorted(
            {task for tool_row in matrix.values() for task in tool_row.keys()},
            key=lambda item: item.lower(),
        )
        if not matrix or not all_task_types:
            return ["- No matrix data available yet."]

        header = ["Tool"] + [task for task in all_task_types]
        lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
        for tool in sorted(matrix.keys(), key=lambda item: item.lower()):
            row = [tool]
            for task in all_task_types:
                cell = matrix.get(tool, {}).get(task, {})
                rate = cell.get("success_rate")
                total = cell.get("total", 0)
                if isinstance(rate, (int, float)):
                    row.append(f"{rate:.0%} ({total})")
                else:
                    row.append(f"n/a ({total})")
            lines.append("| " + " | ".join(row) + " |")
        return lines


def _load_catalog(path: Optional[Path]) -> Dict[str, ToolCatalogEntry]:
    if path is None:
        return {}
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, list):
        return {}
    catalog: Dict[str, ToolCatalogEntry] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        catalog[name] = ToolCatalogEntry(
            name=name,
            description=str(item.get("description", "") or ""),
            tags=tuple(str(t) for t in (item.get("tags", []) or []) if str(t).strip()),
            task_types=tuple(
                _normalize_task_type(str(t)) for t in (item.get("task_types", []) or []) if str(t).strip()
            ),
        )
    return catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tool discovery and recommendations for OpenClaw.")
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="Directory where tool usage logs and snapshots are stored (defaults to ~/.openclaw/logs).",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help="Optional JSON catalog file (list of {name, description, tags, task_types}).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="Record a tool usage event.")
    record.add_argument("--tool", required=True)
    record.add_argument("--task-type", required=True)
    record.add_argument("--success", action="store_true", help="Mark usage as success (default: failure).")
    record.add_argument("--skill", action="append", default=[], help="Skill used (repeatable).")
    record.add_argument("--context", default="{}", help="Optional JSON object with context metadata.")
    record.add_argument("--duration", default=None, help="Optional duration seconds.")
    record.add_argument("--error", default=None, help="Optional error message.")

    report = subparsers.add_parser("report", help="Generate markdown recommendations.")
    report.add_argument("--task-type", required=True)
    report.add_argument("--context-text", default="")
    report.add_argument("--recommend-window-days", type=int, default=90)
    report.add_argument("--matrix-window-days", type=int, default=30)
    report.add_argument("--top-n", type=int, default=5)

    snapshot = subparsers.add_parser("snapshot", help="Write a dashboard JSON snapshot.")
    snapshot.add_argument("--window-days", type=int, default=30)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    catalog = _load_catalog(args.catalog)
    engine = ToolDiscoveryEngine(log_dir=args.log_dir, tool_catalog=catalog)

    if args.command == "record":
        try:
            context = json.loads(args.context)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--context must be valid JSON: {exc}") from exc
        if not isinstance(context, dict):
            raise SystemExit("--context must decode to a JSON object")
        engine.record_usage(
            tool=args.tool,
            task_type=args.task_type,
            success=bool(args.success),
            skills=list(args.skill or []),
            context=context,
            duration_seconds=args.duration,
            error=args.error,
        )
        return 0

    if args.command == "report":
        md = engine.generate_recommendations_markdown(
            task_type=args.task_type,
            context_text=args.context_text,
            since_days=args.recommend_window_days,
            top_n=args.top_n,
            matrix_days=args.matrix_window_days,
        )
        print(md)
        return 0

    if args.command == "snapshot":
        path = engine.write_dashboard_snapshot(since_days=args.window_days)
        print(str(path))
        return 0

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

