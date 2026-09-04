#!/usr/bin/env python3
"""
Weekly self-reflection cron: analyze recent sessions, surface patterns, write learnings.

Scans agent logs and session artifacts from the past week (configurable), ranks recurring
mistakes and wins, and writes ``.learnings/weekly-reflections/YYYY-WW.md`` with top errors,
top wins, and recommended ``AGENTS.md`` updates.

Example crontab (Monday 07:00 UTC)::

    0 7 * * 1 cd /path/to/repo && /usr/bin/python3 -m src.self_improvement.auto_reflection

Environment (optional unless posting):

- ``AUTO_REFLECTION_ROOT`` — workspace root (default: cwd)
- ``AUTO_REFLECTION_SESSION_GLOBS`` — extra comma-separated globs under root
- ``AUTO_REFLECTION_SESSION_DIRS`` — extra directories to scan (comma-separated)
- ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID`` — post summary via Telegram
- ``REFLECTION_WEBHOOK_URL`` — POST JSON summary webhook
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from scripts.auto_reflection import (
    Insight,
    collect_globs,
    dedupe_insights,
    iter_session_files,
    maybe_post_results,
    read_and_extract,
    rel_under_root,
    session_dirs_from_env,
    utc_now,
)

LEARNINGS_DIR = ".learnings"
WEEKLY_REFLECTIONS_SUBDIR = "weekly-reflections"
STATE_NAME = ".state.json"

DEFAULT_SINCE_HOURS = 24 * 7

_SEV_RANK = {"error": 3, "warning": 2, "info": 1}
_CAT_ERROR_BONUS = {"loss": 4, "lesson": 3, "integration": 2, "testing": 2, "general": 1, "win": 0, "git": 1}


@dataclass
class WeeklyReflectionRun:
    """Result of one weekly reflection pass."""

    run_id: str
    iso_week: str
    started_at_utc: str
    finished_at_utc: str
    files_scanned: int
    session_files: list[str]
    insights: list[Insight]
    top_errors: list[Insight]
    top_wins: list[Insight]
    agents_md_recommendations: list[str]
    report_markdown: str
    report_path: str = ""


def _state_path(root: Path) -> Path:
    return root / LEARNINGS_DIR / STATE_NAME


def load_state(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(root: Path, data: dict[str, Any]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iso_week_label(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def weekly_report_path(root: Path, dt: datetime, *, week_label: str | None = None) -> Path:
    label = week_label or iso_week_label(dt)
    return root / LEARNINGS_DIR / WEEKLY_REFLECTIONS_SUBDIR / f"{label}.md"


def _insight_score_error(ins: Insight) -> tuple[int, int, str]:
    sev = _SEV_RANK.get(ins.severity, 0)
    cat = _CAT_ERROR_BONUS.get(ins.category, 0)
    if ins.category == "win":
        return (0, 0, ins.text.lower())
    return (sev + cat, len(ins.source_paths), ins.text.lower())


def _insight_score_win(ins: Insight) -> tuple[int, str]:
    return (len(ins.source_paths), ins.text.lower())


def rank_top_errors(insights: Sequence[Insight], *, limit: int = 3) -> list[Insight]:
    candidates = [
        i
        for i in insights
        if i.category in ("loss", "lesson", "integration", "testing")
        or i.severity in ("error", "warning")
    ]
    if not candidates:
        candidates = [i for i in insights if i.severity == "error" or i.category == "loss"]
    ranked = sorted(candidates, key=_insight_score_error, reverse=True)
    return ranked[:limit]


def rank_top_wins(insights: Sequence[Insight], *, limit: int = 3) -> list[Insight]:
    wins = [i for i in insights if i.category == "win"]
    if not wins:
        wins = [i for i in insights if i.category == "lesson" and i.severity == "info"]
    ranked = sorted(wins, key=_insight_score_win, reverse=True)
    return ranked[:limit]


def _agents_md_excerpt(root: Path, max_chars: int = 4000) -> str:
    path = root / "AGENTS.md"
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def _already_covered(suggestion: str, agents_text: str) -> bool:
    if not agents_text.strip():
        return False
    tokens = [t for t in re.findall(r"[a-z0-9]{5,}", suggestion.lower()) if len(t) >= 5]
    if not tokens:
        return False
    hits = sum(1 for t in tokens[:6] if t in agents_text.lower())
    return hits >= min(3, len(tokens))


def recommend_agents_md_updates(
    insights: Sequence[Insight],
    top_errors: Sequence[Insight],
    top_wins: Sequence[Insight],
    root: Path,
    *,
    limit: int = 3,
) -> list[str]:
    """Derive concrete AGENTS.md edit suggestions from weekly patterns."""

    agents_text = _agents_md_excerpt(root)
    suggestions: list[str] = []
    seen: set[str] = set()

    def add(line: str) -> None:
        norm = re.sub(r"\s+", " ", line.strip())
        if len(norm) < 20 or norm in seen:
            return
        if _already_covered(norm, agents_text):
            return
        seen.add(norm)
        suggestions.append(norm)

    for err in top_errors:
        src = err.source_paths[0] if err.source_paths else "sessions"
        add(
            f"Under **Troubleshooting** or **Common failures**, document: "
            f"\"{err.text}\" (seen in `{src}`)."
        )

    lessons = [i for i in insights if i.category == "lesson"]
    for lesson in sorted(lessons, key=lambda i: len(i.source_paths), reverse=True)[:5]:
        add(f"Add agent rule: {lesson.text}")

    if top_wins:
        win = top_wins[0]
        add(
            f"Reinforce what worked: keep \"{win.text[:160]}\" as a positive pattern "
            f"when similar tasks appear."
        )

    ctr = Counter(i.category for i in insights if i.category not in ("win", "general"))
    for cat, count in ctr.most_common(2):
        if count < 2:
            continue
        add(
            f"Expand **{cat}** guidance in AGENTS.md — {count} related signals this week "
            f"(timeouts, flaky tools, or unclear scope)."
        )

    if not agents_text.strip():
        add(
            "Create `AGENTS.md` with sections: Goals, Tooling constraints, "
            "Troubleshooting, and Lessons from weekly reflection."
        )

    if not suggestions:
        add(
            "Review top errors and wins below; add one explicit Do/Don't bullet per recurring theme."
        )

    return suggestions[:limit]


def _format_insight_bullet(ins: Insight, *, numbered: bool, index: int) -> str:
    prefix = f"{index}. " if numbered else "- "
    sources = ", ".join(f"`{s}`" for s in ins.source_paths[:2])
    if len(ins.source_paths) > 2:
        sources += ", …"
    badge = f"**[{ins.severity.upper()}]** " if ins.severity in ("error", "warning") else ""
    src_suffix = f" _(sources: {sources})_" if sources else ""
    return f"{prefix}{badge}{ins.text}{src_suffix}"


def build_weekly_report_markdown(
    run_at: datetime,
    *,
    files_scanned: int,
    window_start: datetime,
    window_end: datetime,
    top_errors: Sequence[Insight],
    top_wins: Sequence[Insight],
    agents_recommendations: Sequence[str],
    insights: Sequence[Insight],
) -> str:
    week = iso_week_label(run_at)
    lines = [
        f"# Weekly reflection — {week}",
        "",
        f"- **Generated (UTC)**: {run_at.replace(microsecond=0).isoformat()}",
        f"- **Window**: {window_start.date().isoformat()} → {window_end.date().isoformat()}",
        f"- **Session files scanned**: {files_scanned}",
        f"- **Distinct insights**: {len(insights)}",
        "",
        "## Top 3 errors",
        "",
    ]

    if top_errors:
        for i, ins in enumerate(top_errors, start=1):
            lines.append(_format_insight_bullet(ins, numbered=True, index=i))
    else:
        lines.append("_No error-class patterns surfaced in the scanned window._")

    lines.extend(["", "## Top 3 wins", ""])
    if top_wins:
        for i, ins in enumerate(top_wins, start=1):
            lines.append(_format_insight_bullet(ins, numbered=True, index=i))
    else:
        lines.append("_No win signals detected; capture explicit decisions and completions in logs._")

    lines.extend(["", "## Recommended AGENTS.md updates", ""])
    if agents_recommendations:
        for rec in agents_recommendations:
            lines.append(f"- {rec}")
    else:
        lines.append("_No specific edits suggested; maintain current agent guidance._")

    if insights:
        lines.extend(["", "## Category snapshot", ""])
        for cat, count in Counter(i.category for i in insights).most_common():
            lines.append(f"- **{cat}**: {count}")

    return "\n".join(lines).rstrip() + "\n"


def collect_insights(
    root: Path,
    *,
    since_hours: float,
    extra_globs: Sequence[str],
) -> tuple[list[Path], list[Insight], datetime, datetime]:
    ended = utc_now()
    started_window = ended - timedelta(hours=since_hours)
    globs = collect_globs(extra_globs)
    session_files = iter_session_files(
        root,
        globs,
        started_window,
        extra_roots=session_dirs_from_env(),
    )

    insights: list[Insight] = []
    for sf in session_files:
        insights.extend(read_and_extract(sf, root))

    insights = dedupe_insights(insights)
    insights.sort(key=lambda x: (x.category, x.severity, x.text))
    return session_files, insights, started_window, ended


def run_weekly_reflection(
    root: Path,
    *,
    since_hours: float = DEFAULT_SINCE_HOURS,
    extra_globs: Sequence[str] = (),
    dry_run: bool = False,
    force: bool = False,
    week_label: str | None = None,
) -> WeeklyReflectionRun:
    started = utc_now()
    run_id = started.strftime("%Y%m%d_%H%M%S")
    week = week_label or iso_week_label(started)

    session_files, insights, window_start, window_end = collect_insights(
        root,
        since_hours=since_hours,
        extra_globs=extra_globs,
    )

    top_errors = rank_top_errors(insights)
    top_wins = rank_top_wins(insights)
    agents_recs = recommend_agents_md_updates(insights, top_errors, top_wins, root)

    report_md = build_weekly_report_markdown(
        started,
        files_scanned=len(session_files),
        window_start=window_start,
        window_end=window_end,
        top_errors=top_errors,
        top_wins=top_wins,
        agents_recommendations=agents_recs,
        insights=insights,
    )

    finished = utc_now()
    report_path = weekly_report_path(root, started, week_label=week)

    run = WeeklyReflectionRun(
        run_id=run_id,
        iso_week=week,
        started_at_utc=started.replace(microsecond=0).isoformat(),
        finished_at_utc=finished.replace(microsecond=0).isoformat(),
        files_scanned=len(session_files),
        session_files=[rel_under_root(p, root) for p in session_files],
        insights=insights,
        top_errors=top_errors,
        top_wins=top_wins,
        agents_md_recommendations=agents_recs,
        report_markdown=report_md,
        report_path=report_path.relative_to(root).as_posix() if report_path.is_relative_to(root) else str(report_path),
    )

    if dry_run:
        return run

    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.exists() and not force:
        existing = report_path.read_text(encoding="utf-8")
        if f"**Generated (UTC)**" in existing and week in existing.splitlines()[0]:
            run.report_path = report_path.relative_to(root).as_posix()
            return run

    report_path.write_text(report_md, encoding="utf-8")
    run.report_path = report_path.relative_to(root).as_posix()

    ptr = root / LEARNINGS_DIR / "latest_weekly.json"
    ptr.write_text(
        json.dumps(
            {
                "iso_week": week,
                "weekly_reflection_md": run.report_path,
                "run_id": run_id,
                "generated_at_utc": finished.replace(microsecond=0).isoformat(),
                "top_error_count": len(top_errors),
                "top_win_count": len(top_wins),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    state = load_state(root)
    state["last_weekly_run_utc"] = finished.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state["last_weekly_run_id"] = run_id
    state["last_weekly_iso_week"] = week
    state["last_weekly_report"] = run.report_path
    save_state(root, state)

    try:
        from src.coordination.iskra_kara_shared_memory import notify_kara_from_iskra

        notify_kara_from_iskra(
            "weekly_reflection",
            {
                "iso_week": week,
                "report_path": run.report_path,
                "top_errors": [i.text for i in top_errors],
                "top_wins": [i.text for i in top_wins],
                "agents_md_recommendations": agents_recs,
                "files_scanned": run.files_scanned,
            },
        )
    except Exception:
        pass

    return run


def _reflection_run_adapter(weekly: WeeklyReflectionRun) -> Any:
    """Adapt weekly run for scripts.auto_reflection.maybe_post_results."""

    from scripts.auto_reflection import ReflectionRun

    return ReflectionRun(
        run_id=weekly.run_id,
        started_at_utc=weekly.started_at_utc,
        finished_at_utc=weekly.finished_at_utc,
        files_scanned=weekly.files_scanned,
        session_files=weekly.session_files,
        insights=weekly.insights,
        summary_markdown=weekly.report_markdown,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Weekly agent self-reflection: scan sessions, write .learnings/weekly-reflections/YYYY-WW.md",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Workspace root (default: AUTO_REFLECTION_ROOT or cwd).",
    )
    parser.add_argument(
        "--since-hours",
        type=float,
        default=DEFAULT_SINCE_HOURS,
        help=f"Hours of history to analyze (default: {DEFAULT_SINCE_HOURS}).",
    )
    parser.add_argument(
        "--glob",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Additional glob relative to root (repeatable).",
    )
    parser.add_argument(
        "--week",
        metavar="YYYY-WW",
        default=None,
        help="ISO week label for the report filename (default: current UTC week).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing report for this week.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze only; do not write files or POST.",
    )
    parser.add_argument(
        "--stdout-summary",
        action="store_true",
        help="Print the weekly markdown report to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    repo_s = str(repo)
    if repo_s not in sys.path:
        sys.path.insert(0, repo_s)

    args = build_parser().parse_args(argv)
    root = args.root or Path(os.environ.get("AUTO_REFLECTION_ROOT", "") or ".").resolve()

    if args.dry_run:
        print(f"[dry-run] root={root}", file=sys.stderr)

    run = run_weekly_reflection(
        root,
        since_hours=args.since_hours,
        extra_globs=args.glob,
        dry_run=args.dry_run,
        force=args.force,
        week_label=args.week,
    )

    for line in maybe_post_results(_reflection_run_adapter(run), dry_run=args.dry_run):
        print(line, file=sys.stderr)

    if args.stdout_summary:
        print(run.report_markdown)

    if not args.dry_run:
        print(f"Weekly reflection written: {run.report_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
