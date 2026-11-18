#!/usr/bin/env python3
"""
Daily self-reflection over recent agent logs and session transcripts.

Analyzes session-shaped content for role/tool flow, derives lightweight metrics
(task completion signals, context switches, tool outcome heuristics), merges
pattern insights from logs, and writes ``.learnings/reflections.md`` plus the
existing insight / weekly artifacts.

Daily cron (08:00 UTC example)::

    0 8 * * * cd /path/to/repo && /usr/bin/python3 -m scripts.self_improvement.auto_reflection

Environment (optional unless posting):

- ``AUTO_REFLECTION_ROOT`` — workspace root (default: cwd)
- ``AUTO_REFLECTION_SESSION_GLOBS`` — extra comma-separated globs under root
- ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID`` — Telegram ``sendMessage``
- ``REFLECTION_WEBHOOK_URL`` — POST JSON ``{\"text\": \"...\", \"meta\": {...}}``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from scripts.conversation_extractor import parse_session_log

LEARNINGS_DIR = ".learnings"
INSIGHTS_SUBDIR = "insights"
SUMMARIES_SUBDIR = "summaries"
REFLECTIONS_NAME = "reflections.md"
STATE_NAME = ".state.json"

DEFAULT_SINCE_HOURS = 24 * 7
DEFAULT_SESSION_GLOBS = (
    "logs/**/*.log",
    "logs/**/*.json",
    "memory/**/*_log.md",
    "memory/**/*.md",
)

FAILURE_HINTS = re.compile(
    r"(?i)(\b(traceback|exception|error|failed|failure|timed?\s*out|exit\s*code\s*[1-9]\d*)\b|"
    r"\b(Fatal|Critical)\b|^Error:|\[\s*ERROR\s*\])"
)
LESSON_HINTS = re.compile(
    r"(?i)(\blesson learned\b|\btakeaway\b|\bremember to\b|\bnext time\b|"
    r"\bavoid\b|\bshould have\b|\broot cause\b)"
)

COMPLETION_HINT = re.compile(
    r"(?i)\b(completed|done|resolved|fixed|success|passed|shipped|merged|green build|all tests pass)\b"
)
BLOCKER_HINT = re.compile(
    r"(?i)\b(failed|failure|blocked|stuck|unable to|cannot\b|timeout|error:|traceback)\b"
)

MAX_FILE_BYTES = 2 * 1024 * 1024
TELEGRAM_TEXT_LIMIT = 4000
REFLECTIONS_MAX_LINES = 500


def _norm_role(role: str | None) -> str:
    if not role:
        return "unknown"
    r = role.strip().lower()
    mapping = {"human": "user", "agent": "assistant"}
    return mapping.get(r, r)


@dataclass
class SessionMetrics:
    """Heuristic metrics from one transcript or log parse."""

    source: str
    segment_rows: int
    context_switches: int
    tool_invocations: int
    tool_output_rows: int
    tool_success_estimated: int
    tool_failure_estimated: int
    completion_signal_hits: int
    blocker_signal_hits: int

    @property
    def task_completion_rate(self) -> float | None:
        d = self.completion_signal_hits + self.blocker_signal_hits
        if d == 0:
            return None
        return self.completion_signal_hits / d

    @property
    def tool_success_rate(self) -> float | None:
        d = self.tool_success_estimated + self.tool_failure_estimated
        if d == 0:
            return None
        return self.tool_success_estimated / d


@dataclass
class AggregatedSessionMetrics:
    files_parsed: int
    segment_rows: int
    context_switches: int
    tool_invocations: int
    tool_output_rows: int
    tool_success_estimated: int
    tool_failure_estimated: int
    completion_signal_hits: int
    blocker_signal_hits: int

    @property
    def task_completion_rate(self) -> float | None:
        d = self.completion_signal_hits + self.blocker_signal_hits
        if d == 0:
            return None
        return self.completion_signal_hits / d

    @property
    def tool_success_rate(self) -> float | None:
        d = self.tool_success_estimated + self.tool_failure_estimated
        if d == 0:
            return None
        return self.tool_success_estimated / d

    @property
    def context_switches_per_100_segments(self) -> float | None:
        if self.segment_rows == 0:
            return None
        return 100.0 * self.context_switches / self.segment_rows


def metrics_from_segments(rel_source: str, segments: list[tuple[int, str | None, str]]) -> SessionMetrics:
    switches = 0
    for i in range(1, len(segments)):
        a = _norm_role(segments[i - 1][1])
        b = _norm_role(segments[i][1])
        if a != b:
            switches += 1

    tool_inv = 0
    tool_out = 0
    tool_ok = 0
    tool_bad = 0
    comp = 0
    block = 0

    for _turn, role, text in segments:
        rl = (role or "").lower()
        if rl == "tool":
            tool_inv += 1
        if rl == "tool_output":
            tool_out += 1
            if FAILURE_HINTS.search(text):
                tool_bad += 1
            else:
                tool_ok += 1
        if rl != "tool" and text.strip():
            if COMPLETION_HINT.search(text):
                comp += 1
            if BLOCKER_HINT.search(text):
                block += 1

    return SessionMetrics(
        source=rel_source,
        segment_rows=len(segments),
        context_switches=switches,
        tool_invocations=tool_inv,
        tool_output_rows=tool_out,
        tool_success_estimated=tool_ok,
        tool_failure_estimated=tool_bad,
        completion_signal_hits=comp,
        blocker_signal_hits=block,
    )


def _metrics_from_plain_text(rel_source: str, raw: str) -> SessionMetrics:
    """Fallback when structured segments are empty: scan raw lines."""

    lines = [ln.strip() for ln in raw.splitlines() if len(ln.strip()) >= 8]
    switches = 0
    prev_cat: str | None = None
    for ln in lines:
        m = re.match(
            r"^\s*(?P<role>user|human|assistant|agent|tool|system)\s*[:|\\-]+\s*(?P<body>.+)$",
            ln,
            re.I,
        )
        cat = _norm_role(m.group("role")) if m else "unknown"
        if prev_cat is not None and cat != prev_cat:
            switches += 1
        prev_cat = cat

    comp = sum(1 for ln in lines if COMPLETION_HINT.search(ln))
    block = sum(1 for ln in lines if BLOCKER_HINT.search(ln))
    tool_out = sum(1 for ln in lines if re.search(r"(?i)\b(tool[_\s]?output|tool result)\b", ln))
    tool_bad = sum(1 for ln in lines if FAILURE_HINTS.search(ln))

    return SessionMetrics(
        source=rel_source,
        segment_rows=len(lines),
        context_switches=switches,
        tool_invocations=0,
        tool_output_rows=tool_out,
        tool_success_estimated=max(0, tool_out - tool_bad),
        tool_failure_estimated=min(tool_bad, tool_out) if tool_out else tool_bad,
        completion_signal_hits=comp,
        blocker_signal_hits=block,
    )


def aggregate_session_metrics(rows: Iterable[SessionMetrics]) -> AggregatedSessionMetrics:
    acc = AggregatedSessionMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0)
    for m in rows:
        acc.files_parsed += 1
        acc.segment_rows += m.segment_rows
        acc.context_switches += m.context_switches
        acc.tool_invocations += m.tool_invocations
        acc.tool_output_rows += m.tool_output_rows
        acc.tool_success_estimated += m.tool_success_estimated
        acc.tool_failure_estimated += m.tool_failure_estimated
        acc.completion_signal_hits += m.completion_signal_hits
        acc.blocker_signal_hits += m.blocker_signal_hits
    return acc


def build_went_well_and_improve(
    agg: AggregatedSessionMetrics,
    insights: Sequence["Insight"],
) -> tuple[list[str], list[str]]:
    went_well: list[str] = []
    improve: list[str] = []

    err_like = sum(1 for i in insights if i.severity == "error")
    warn_like = sum(1 for i in insights if i.severity == "warning")

    tcr = agg.task_completion_rate
    tsr = agg.tool_success_rate
    sw_rate = agg.context_switches_per_100_segments

    if agg.files_parsed == 0 and agg.segment_rows == 0:
        improve.append("No parseable session rows in the window; widen globs or check log freshness.")
    else:
        if tcr is not None and tcr >= 0.55:
            went_well.append(
                f"Completion-oriented phrasing outnumbered friction markers in scanned text "
                f"(estimated task signal rate **{tcr:.0%}**)."
            )
        elif tcr is not None and tcr < 0.45:
            improve.append(
                f"Friction markers were common versus completion language "
                f"(estimated task signal rate **{tcr:.0%}**); tighten error handling or scope."
            )

        if tsr is not None and tsr >= 0.82 and agg.tool_output_rows >= 3:
            went_well.append(
                f"Tool outputs were mostly clean versus error heuristics "
                f"(**{tsr:.0%}** success estimate over **{agg.tool_output_rows}** tool-result rows)."
            )
        elif tsr is not None and tsr < 0.55 and agg.tool_output_rows >= 3:
            improve.append(
                f"Tool-result rows skew toward failures/timeouts in heuristics "
                f"(**{tsr:.0%}** estimated success); review flaky tools or arguments."
            )

        if sw_rate is not None and sw_rate <= 35:
            went_well.append(
                f"Role flow stayed relatively steady (**{sw_rate:.1f}** context switches per 100 segments)."
            )
        elif sw_rate is not None and sw_rate >= 70:
            improve.append(
                f"High role churn (**{sw_rate:.1f}** switches per 100 segments); batch tool work "
                f"and reduce assistant/user ping-pong where possible."
            )

    if err_like == 0 and warn_like <= 2 and len(insights) > 0:
        went_well.append("Log scan surfaced lessons or info-level notes with few hard errors.")
    if err_like >= 3:
        improve.append(f"Several error-class log lines deduped into **{err_like}** distinct error insights.")
    if warn_like >= 6:
        improve.append(f"Elevated warning-style signals (**{warn_like}** insights); consider addressing recurring warnings first.")

    if not went_well:
        went_well.append("Maintain the habit of capturing explicit decisions and lessons in session logs.")
    if not improve:
        improve.append("Keep scanning for slow tests, integration timeouts, and ambiguous requirements early.")

    return went_well, improve


def build_reflections_markdown(
    run_at: datetime,
    run_id: str,
    agg: AggregatedSessionMetrics,
    went_well: Sequence[str],
    improve: Sequence[str],
    top_insights: Sequence["Insight"],
) -> str:
    lines = [
        f"## {run_at.date().isoformat()} — run `{run_id}` (UTC)",
        "",
        "### Metrics",
        "",
        f"- **Session files parsed**: {agg.files_parsed}",
        f"- **Segment rows**: {agg.segment_rows}",
        f"- **Context switches** (role transitions): **{agg.context_switches}**",
    ]
    csr = agg.context_switches_per_100_segments
    lines.append(
        f"- **Context switches / 100 segments**: "
        f"**{csr:.1f}**" if csr is not None else "- **Context switches / 100 segments**: _n/a_"
    )
    lines.extend(
        [
            f"- **Tool invocations (structured)**: {agg.tool_invocations}",
            f"- **Tool output rows**: {agg.tool_output_rows}",
            f"- **Tool success (heuristic)**: {agg.tool_success_estimated}",
            f"- **Tool failure (heuristic)**: {agg.tool_failure_estimated}",
        ]
    )
    tcr = agg.task_completion_rate
    tsr = agg.tool_success_rate
    lines.append(
        f"- **Task completion rate (heuristic)**: **{tcr:.0%}**"
        if tcr is not None
        else "- **Task completion rate (heuristic)**: _insufficient completion/friction signals_"
    )
    lines.append(
        f"- **Tool success rate (heuristic)**: **{tsr:.0%}**"
        if tsr is not None
        else "- **Tool success rate (heuristic)**: _no tool-result rows parsed_"
    )
    lines.extend(["", "### What went well", ""])
    for w in went_well:
        lines.append(f"- {w}")
    lines.extend(["", "### What could improve", ""])
    for x in improve:
        lines.append(f"- {x}")

    if top_insights:
        lines.extend(["", "### Pattern highlights", ""])
        rank = {"error": 0, "warning": 1, "info": 2}
        for ins in sorted(top_insights, key=lambda i: (rank.get(i.severity, 9), i.text.lower()))[:12]:
            lines.append(f"- **[{ins.severity.upper()}]** {ins.text}")

    lines.append("")
    return "\n".join(lines)


def update_reflections_md(root: Path, chunk: str) -> Path:
    path = root / LEARNINGS_DIR / REFLECTIONS_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "# Auto-reflection journal\n\n_Concise daily / periodic notes. Newest sections first._\n\n"
    divider = "\n---\n\n"
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if old.strip().startswith("# Auto-reflection"):
            parts = old.split("---", 1)
            rest = parts[1].lstrip("\n") if len(parts) == 2 else ""
        else:
            rest = old.lstrip("\n")
        merged = header + chunk.strip() + divider + rest
    else:
        merged = header + chunk.strip() + "\n"

    lines = merged.splitlines()
    if len(lines) > REFLECTIONS_MAX_LINES:
        head_n = min(100, len(lines) // 4)
        merged = (
            "\n".join(lines[:head_n])
            + "\n\n_…trimmed middle for size; kept head and tail._\n\n"
            + "\n".join(lines[-(REFLECTIONS_MAX_LINES - head_n - 5) :])
        )

    path.write_text(merged, encoding="utf-8")
    return path


@dataclass
class Insight:
    text: str
    source_paths: list[str] = field(default_factory=list)
    severity: str = "info"
    category: str = "general"


@dataclass
class ReflectionRun:
    run_id: str
    started_at_utc: str
    finished_at_utc: str
    files_scanned: int
    session_files: list[str]
    insights: list[Insight]
    summary_markdown: str
    aggregated_metrics: dict[str, Any] = field(default_factory=dict)
    reflections_markdown: str = ""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def iter_session_files(root: Path, globs: Sequence[str], cutoff: datetime) -> list[Path]:
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
            if st.st_size > MAX_FILE_BYTES:
                continue
            rp = path.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            out.append(path)
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def _severity_for_line(line: str) -> str:
    if re.search(r"(?i)\b(traceback|exception|fatal|critical)\b", line):
        return "error"
    if FAILURE_HINTS.search(line):
        return "warning"
    return "info"


def _category_for_line(line: str) -> str:
    if LESSON_HINTS.search(line):
        return "lesson"
    if re.search(r"(?i)\b(test|pytest|unittest)\b", line):
        return "testing"
    if re.search(r"(?i)\b(git|commit|merge|branch)\b", line):
        return "git"
    if re.search(r"(?i)\b(api|http|request|timeout)\b", line):
        return "integration"
    return "general"


def normalize_insight_text(line: str) -> str:
    line = line.strip()
    line = re.sub(r"\s+", " ", line)
    return line[:500]


def insight_fingerprint(text: str) -> str:
    return hashlib.sha256(text.lower().encode("utf-8")).hexdigest()[:16]


def extract_insights_from_text(path: Path, root: Path, raw: str) -> Iterator[Insight]:
    rel = path.relative_to(root).as_posix()
    for line in raw.splitlines():
        stripped = line.strip()
        if len(stripped) < 12:
            continue
        if FAILURE_HINTS.search(stripped) or LESSON_HINTS.search(stripped):
            text = normalize_insight_text(stripped)
            if not text:
                continue
            yield Insight(
                text=text,
                source_paths=[rel],
                severity=_severity_for_line(stripped),
                category=_category_for_line(stripped),
            )


def extract_insights_from_json(path: Path, root: Path, raw: str) -> Iterator[Insight]:
    rel = path.relative_to(root).as_posix()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        yield from extract_insights_from_text(path, root, raw)
        return

    strings: list[str] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(k, str) and re.search(r"(?i)\b(error|stderr|message|detail)\b", k):
                    if isinstance(v, str) and v.strip():
                        strings.append(v.strip())
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
        elif isinstance(obj, str) and FAILURE_HINTS.search(obj):
            strings.append(obj.strip())

    walk(data)
    for s in strings:
        for insight in extract_insights_from_text(path, root, s):
            if rel not in insight.source_paths:
                insight.source_paths.insert(0, rel)
            yield insight


def read_and_extract(path: Path, root: Path) -> list[Insight]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if path.suffix.lower() == ".json":
        return list(extract_insights_from_json(path, root, raw))
    return list(extract_insights_from_text(path, root, raw))


def dedupe_insights(insights: Iterable[Insight]) -> list[Insight]:
    buckets: dict[str, Insight] = {}
    for ins in insights:
        fp = insight_fingerprint(ins.text)
        existing = buckets.get(fp)
        if existing is None:
            buckets[fp] = Insight(
                text=ins.text,
                source_paths=list(ins.source_paths),
                severity=ins.severity,
                category=ins.category,
            )
        else:
            for p in ins.source_paths:
                if p not in existing.source_paths:
                    existing.source_paths.append(p)
            sev_rank = {"error": 3, "warning": 2, "info": 1}
            if sev_rank.get(ins.severity, 0) > sev_rank.get(existing.severity, 0):
                existing.severity = ins.severity
    return list(buckets.values())


def build_summary_markdown(
    run_at: datetime,
    files_scanned: int,
    insights: Sequence[Insight],
    top_sessions: Sequence[str],
    agg: AggregatedSessionMetrics,
) -> str:
    lines = [
        f"# Reflection summary ({run_at.date().isoformat()} UTC)",
        "",
        f"- Session files scanned: **{files_scanned}**",
        f"- Distinct insights: **{len(insights)}**",
        "",
        "## Session metrics (heuristic)",
        f"- Files contributing parsed segments: **{agg.files_parsed}**",
        f"- Context switches: **{agg.context_switches}**",
    ]
    tcr = agg.task_completion_rate
    tsr = agg.tool_success_rate
    csr = agg.context_switches_per_100_segments
    lines.append(
        f"- Task completion rate: **{tcr:.0%}**" if tcr is not None else "- Task completion rate: _n/a_"
    )
    lines.append(
        f"- Tool success rate: **{tsr:.0%}**" if tsr is not None else "- Tool success rate: _n/a_"
    )
    lines.append(
        f"- Context switches / 100 segments: **{csr:.1f}**" if csr is not None else "- Context switches / 100 segments: _n/a_"
    )
    lines.append("")

    if top_sessions:
        lines.append("## Recently touched logs")
        for p in top_sessions[:15]:
            lines.append(f"- `{p}`")
        lines.append("")
    if not insights:
        lines.append("_No notable patterns in the scanned window._")
        return "\n".join(lines)

    lines.append("## Insights")
    by_cat: dict[str, list[Insight]] = {}
    for ins in insights:
        by_cat.setdefault(ins.category, []).append(ins)

    for cat in sorted(by_cat.keys()):
        lines.append(f"### {cat}")
        rank = {"error": 0, "warning": 1, "info": 2}
        for ins in sorted(
            by_cat[cat],
            key=lambda i: (rank.get(i.severity, 9), i.text.lower()),
        ):
            badge = ins.severity.upper()
            sources = ", ".join(f"`{s}`" for s in ins.source_paths[:3])
            if len(ins.source_paths) > 3:
                sources += ", …"
            lines.append(f"- **[{badge}]** {ins.text} _(sources: {sources})_")
        lines.append("")

    ctr = Counter(i.category for i in insights)
    lines.append("## Category counts")
    for cat, n in ctr.most_common():
        lines.append(f"- **{cat}**: {n}")
    return "\n".join(lines).rstrip() + "\n"


def weekly_report_path(root: Path, dt: datetime) -> Path:
    iso = dt.isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"
    return root / LEARNINGS_DIR / SUMMARIES_SUBDIR / f"weekly_{week}.md"


def update_weekly_summary(root: Path, run_at: datetime, body: str) -> Path:
    path = weekly_report_path(root, run_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    day_heading = f"\n## {run_at.date().isoformat()} (UTC)\n\n"
    chunk = day_heading + body + "\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if run_at.date().isoformat() in existing:
            return path
        path.write_text(existing.rstrip() + "\n" + chunk, encoding="utf-8")
    else:
        path.write_text(
            f"# Weekly reflection — {run_at.isocalendar().year} W{run_at.isocalendar().week:02d}\n" + chunk
        )
    return path


def write_insight_artifacts(root: Path, run: ReflectionRun) -> tuple[Path, Path]:
    insights_dir = root / LEARNINGS_DIR / INSIGHTS_SUBDIR
    insights_dir.mkdir(parents=True, exist_ok=True)
    base = insights_dir / f"run_{run.run_id}"
    md_path = base.with_suffix(".md")
    json_path = base.with_suffix(".json")

    md_lines = [
        f"# Run {run.run_id}",
        f"- Started: {run.started_at_utc}",
        f"- Finished: {run.finished_at_utc}",
        f"- Files scanned: {run.files_scanned}",
        "",
        run.summary_markdown,
    ]
    if run.reflections_markdown.strip():
        md_lines.extend(["", "## Reflections excerpt", "", run.reflections_markdown.strip(), ""])

    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    json_path.write_text(json.dumps(asdict(run), indent=2) + "\n", encoding="utf-8")
    return md_path, json_path


def write_latest_pointers(root: Path, md_path: Path, weekly_path: Path, reflections_path: Path) -> None:
    ptr = root / LEARNINGS_DIR / "latest.json"
    data = {
        "insights_md": md_path.relative_to(root).as_posix(),
        "weekly_summary_md": weekly_path.relative_to(root).as_posix(),
        "reflections_md": reflections_path.relative_to(root).as_posix(),
        "generated_at_utc": utc_now().replace(microsecond=0).isoformat(),
    }
    ptr.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def post_webhook(url: str, payload: dict[str, Any]) -> tuple[bool, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")[:500]
            return True, raw or "ok"
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except OSError:
            detail = str(exc)
        return False, detail
    except urllib.error.URLError as exc:
        return False, str(exc.reason if hasattr(exc, "reason") else exc)


def post_telegram_summary(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks: list[str] = []
    remaining = text
    while remaining:
        chunks.append(remaining[:TELEGRAM_TEXT_LIMIT])
        remaining = remaining[TELEGRAM_TEXT_LIMIT:]

    last_msg = ""
    for i, chunk in enumerate(chunks):
        prefix = f"(part {i + 1}/{len(chunks)})\n" if len(chunks) > 1 else ""
        body = json.dumps(
            {"chat_id": chat_id, "text": prefix + chunk, "disable_web_page_preview": True}
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                last_msg = resp.read().decode("utf-8", errors="replace")[:500]
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except OSError:
                detail = str(exc)
            return False, detail
        except urllib.error.URLError as exc:
            return False, str(exc.reason if hasattr(exc, "reason") else exc)
    return True, last_msg or "ok"


def collect_globs(extra: Sequence[str]) -> tuple[str, ...]:
    merged = list(DEFAULT_SESSION_GLOBS)
    env_extra = os.environ.get("AUTO_REFLECTION_SESSION_GLOBS", "")
    if env_extra.strip():
        merged.extend(p.strip() for p in env_extra.split(",") if p.strip())
    merged.extend(extra)
    return tuple(dict.fromkeys(merged))


def collect_per_file_metrics(root: Path, session_files: list[Path]) -> list[SessionMetrics]:
    rows: list[SessionMetrics] = []
    for sf in session_files:
        rel = sf.relative_to(root).as_posix()
        try:
            raw = sf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        segs = parse_session_log(sf)
        if len(segs) >= 2:
            rows.append(metrics_from_segments(rel, segs))
        elif raw.strip():
            rows.append(_metrics_from_plain_text(rel, raw))
    return rows


def run_reflection(
    root: Path,
    *,
    since_hours: float,
    extra_globs: Sequence[str],
    overlap_minutes: int = 90,
    dry_run: bool = False,
) -> ReflectionRun:
    started = utc_now()
    state = load_state(root)
    last_run_s = state.get("last_run_utc")
    if last_run_s:
        try:
            last_dt = datetime.fromisoformat(last_run_s.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            cutoff = last_dt - timedelta(minutes=overlap_minutes)
        except ValueError:
            cutoff = started - timedelta(hours=since_hours)
    else:
        cutoff = started - timedelta(hours=since_hours)

    globs = collect_globs(extra_globs)
    session_files = iter_session_files(root, globs, cutoff)

    per_file = collect_per_file_metrics(root, session_files)
    agg = aggregate_session_metrics(per_file)

    insights: list[Insight] = []
    for sf in session_files:
        insights.extend(read_and_extract(sf, root))

    insights = dedupe_insights(insights)
    insights.sort(key=lambda x: (x.category, x.severity, x.text))

    went_well, improve = build_went_well_and_improve(agg, insights)
    top_for_reflection = sorted(
        insights,
        key=lambda i: ({"error": 0, "warning": 1, "info": 2}.get(i.severity, 9), -len(i.source_paths)),
    )[:12]

    run_id = started.strftime("%Y%m%d_%H%M%S")
    reflections_body = build_reflections_markdown(started, run_id, agg, went_well, improve, top_for_reflection)

    top_sessions = [p.relative_to(root).as_posix() for p in session_files[:20]]
    summary = build_summary_markdown(started, len(session_files), insights, top_sessions, agg)

    finished = utc_now()

    agg_dict = {
        "files_parsed": agg.files_parsed,
        "segment_rows": agg.segment_rows,
        "context_switches": agg.context_switches,
        "context_switches_per_100_segments": agg.context_switches_per_100_segments,
        "tool_invocations": agg.tool_invocations,
        "tool_output_rows": agg.tool_output_rows,
        "tool_success_estimated": agg.tool_success_estimated,
        "tool_failure_estimated": agg.tool_failure_estimated,
        "task_completion_rate": agg.task_completion_rate,
        "tool_success_rate": agg.tool_success_rate,
    }

    run = ReflectionRun(
        run_id=run_id,
        started_at_utc=started.replace(microsecond=0).isoformat(),
        finished_at_utc=finished.replace(microsecond=0).isoformat(),
        files_scanned=len(session_files),
        session_files=[p.relative_to(root).as_posix() for p in session_files],
        insights=insights,
        summary_markdown=summary,
        aggregated_metrics=agg_dict,
        reflections_markdown=reflections_body,
    )

    if dry_run:
        return run

    md_path, _ = write_insight_artifacts(root, run)
    weekly_path = update_weekly_summary(root, started, summary)
    reflections_path = update_reflections_md(root, reflections_body)
    write_latest_pointers(root, md_path, weekly_path, reflections_path)

    state["last_run_utc"] = finished.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state["last_run_id"] = run_id
    state["last_insight_count"] = len(insights)
    state["last_aggregated_metrics"] = agg_dict
    save_state(root, state)

    try:
        from src.coordination.iskra_kara_shared_memory import notify_kara_from_iskra

        notify_kara_from_iskra(
            "reflection",
            {
                "summary_markdown": run.summary_markdown,
                "reflections_markdown": run.reflections_markdown,
                "run_id": run.run_id,
                "files_scanned": run.files_scanned,
                "insight_count": len(run.insights),
                "metrics": run.aggregated_metrics,
            },
        )
    except Exception:
        pass

    return run


def maybe_post_results(run: ReflectionRun, *, dry_run: bool) -> list[str]:
    log: list[str] = []
    text = run.summary_markdown + "\n\n" + run.reflections_markdown
    webhook = os.environ.get("REFLECTION_WEBHOOK_URL", "").strip()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    payload = {
        "text": text,
        "meta": {
            "run_id": run.run_id,
            "started_at": run.started_at_utc,
            "files_scanned": run.files_scanned,
            "insight_count": len(run.insights),
            "metrics": run.aggregated_metrics,
        },
    }

    if dry_run:
        log.append("[dry-run] Skipping webhook and Telegram.")
        return log

    if webhook:
        ok, msg = post_webhook(webhook, payload)
        log.append(f"Webhook {'ok' if ok else 'FAILED'}: {msg[:400]}")

    if token and chat_id:
        ok, msg = post_telegram_summary(token, chat_id, text)
        log.append(f"Telegram {'ok' if ok else 'FAILED'}: {msg[:400]}")
    elif token or chat_id:
        log.append("Telegram skipped: need both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")

    if not webhook and not (token and chat_id):
        log.append("No REFLECTION_WEBHOOK_URL or full Telegram credentials; summary only on disk.")

    return log


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze recent agent logs, refresh .learnings/, and optionally post a summary.",
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
        help=f"Hours of history to include when no prior state exists (default: {DEFAULT_SINCE_HOURS}).",
    )
    parser.add_argument(
        "--glob",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Additional glob relative to root (repeatable).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write files or POST; prints intended actions.",
    )
    parser.add_argument(
        "--stdout-summary",
        action="store_true",
        help="Print the markdown summary to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    root = args.root or Path(os.environ.get("AUTO_REFLECTION_ROOT", "") or ".").resolve()
    if args.dry_run:
        print(f"[dry-run] root={root}", file=sys.stderr)

    run = run_reflection(
        root,
        since_hours=args.since_hours,
        extra_globs=args.glob,
        dry_run=args.dry_run,
    )

    for line in maybe_post_results(run, dry_run=args.dry_run):
        print(line, file=sys.stderr)

    if args.stdout_summary:
        print(run.summary_markdown)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
