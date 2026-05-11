#!/usr/bin/env python3
"""
Cron-friendly self-reflection over recent agent-style logs and session artifacts.

Scans configurable paths for logs and JSON, classifies lines into errors, corrections,
successful completions, and explicit lessons, clusters recurring signals, attaches
heuristic fix hints where possible, writes structured outputs under `.learnings/`
(including `recurring_mistakes.json` and `action_items.md`), builds a periodic summary,
and optionally posts the summary (Telegram or generic webhook).

Example crontab (daily at 09:00 UTC):

    0 9 * * * cd /path/to/repo && /usr/bin/python3 -m scripts.auto_reflection --quiet

Environment (all optional unless posting):

- AUTO_REFLECTION_ROOT — workspace root (default: current working directory)
- AUTO_REFLECTION_SESSION_GLOBS — extra comma-separated glob patterns relative to root
- TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID — post summary via Telegram sendMessage
- REFLECTION_WEBHOOK_URL — POST JSON {\"text\": \"...\", \"meta\": {...}} to this URL
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


LEARNINGS_DIR = ".learnings"
INSIGHTS_SUBDIR = "insights"
SUMMARIES_SUBDIR = "summaries"
STATE_NAME = ".state.json"
RECURRING_JSON = "recurring_mistakes.json"
ACTION_ITEMS_MD = "action_items.md"
ERROR_LOG_JSON = "error_log.json"

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
CORRECTION_HINTS = re.compile(
    r"(?i)(\bcorrection\b|\bactually\b[,:\s]|\binstead (of|use)\b|\bfixed by\b|\bupdated to\b|"
    r"\breplaced\b.+\bwith\b|\bwas wrong\b|\bmistake\b[:\s]|\berrata\b|\buse the correct\b|"
    r"\bthe (real |)issue was\b|\brollback\b|\brevert(ed|ing)?\b)"
)
SUCCESS_HINTS = re.compile(
    r"(?i)(\ball tests passed\b|\btests passed\b|\bcompleted successfully\b|\btask complete\b|"
    r"\b(done|shipped|merged)\b.*\b(PR|pull request|#)\b|\b\[OK\]\b|\bgreen build\b|"
    r"\bCI passed\b|\bdeploy(ed)? successfully\b|\bresolution\b[:\s].*(fixed|resolved|closed)|"
    r"\b(successfully|success)\b.*\b(complet|finish|merg))"
)
MAX_FILE_BYTES = 2 * 1024 * 1024
TELEGRAM_TEXT_LIMIT = 4000
RECURRING_MIN_HITS = 2
CONCISE_MAX_LINES = 14

# (pattern, actionable fix hint) — keep strings concrete, not generic platitudes.
FIX_HEURISTICS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"ModuleNotFoundError|No module named", re.I),
        "Install the missing dependency in the active venv (`pip install` / `uv sync`) or fix "
        "imports/PYTHONPATH so the module resolves before the next run.",
    ),
    (
        re.compile(r"ImportError:\s*cannot import name", re.I),
        "Align symbol names with the package API (check re-exports and `__init__.py`); run a local "
        "import of the failing module to confirm.",
    ),
    (
        re.compile(r"Permission denied|EACCES", re.I),
        "Fix filesystem permissions (`chmod`/`chown`) or write under an allowed directory; avoid "
        "sudo in agent scripts unless explicitly required.",
    ),
    (
        re.compile(r"timed?\s*out|timeout|ReadTimeout|ConnectTimeout", re.I),
        "Raise client/server timeouts, add bounded retries with backoff, and shrink payloads or "
        "batch requests.",
    ),
    (
        re.compile(r"connection refused|ECONNREFUSED|Name or service not known", re.I),
        "Verify the target host/port, that the daemon is running, and DNS/VPN/firewall rules "
        "before retrying.",
    ),
    (
        re.compile(r"\b401\b|\b403\b|Unauthorized|Forbidden|invalid token|expired token", re.I),
        "Rotate or export fresh credentials, confirm OAuth scopes, and check clock skew on the "
        "runner.",
    ),
    (
        re.compile(r"merge conflict|CONFLICT", re.I),
        "Resolve conflicts locally (`git status`), re-run tests, then complete the merge commit "
        "with a clean tree.",
    ),
    (
        re.compile(r"KeyError|AttributeError|TypeError", re.I),
        "Add a failing unit test around the edge case, validate inputs at the boundary, and align "
        "types with the real payload shape.",
    ),
    (
        re.compile(r"FileNotFoundError|ENOENT", re.I),
        "Confirm paths relative to the workspace root; generate missing dirs with `mkdir -p` or "
        "adjust config to the actual artifact location.",
    ),
    (
        re.compile(r"JSONDecodeError|Unexpected token", re.I),
        "Validate JSON at the source (truncated download, HTML error page); re-fetch or tighten "
        "response parsing with explicit error handling.",
    ),
    (
        re.compile(r"exit code\s*[1-9]|command failed|non-zero exit", re.I),
        "Re-run the failing command with verbose flags, capture stderr to a log file, and gate "
        "retries on the specific exit code.",
    ),
)


@dataclass
class Insight:
    """One deduplicated insight line derived from logs."""

    text: str
    source_paths: list[str] = field(default_factory=list)
    severity: str = "info"  # info | warning | error
    category: str = "general"
    pattern_type: str = "general"  # error | correction | success | lesson | general
    hit_count: int = 1
    suggested_fix: str = ""


@dataclass
class ReflectionRun:
    """Serializable result of one reflection pass."""

    run_id: str
    started_at_utc: str
    finished_at_utc: str
    files_scanned: int
    session_files: list[str]
    insights: list[Insight]
    summary_markdown: str
    recurring_from_sessions: list[dict[str, Any]] = field(default_factory=list)
    error_log_highlights: list[dict[str, Any]] = field(default_factory=list)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def classify_pattern_type(line: str) -> str:
    """Prefer hard failures over softer signals when multiple hints match."""

    if FAILURE_HINTS.search(line):
        return "error"
    if LESSON_HINTS.search(line):
        return "lesson"
    if CORRECTION_HINTS.search(line):
        return "correction"
    if SUCCESS_HINTS.search(line):
        return "success"
    return "general"


def suggest_fix_for_line(line: str, pattern_type: str) -> str:
    """Concrete, tool-oriented hints; empty string when the line already implies the fix."""

    if pattern_type == "error":
        for rx, hint in FIX_HEURISTICS:
            if rx.search(line):
                return hint
        return ""
    if pattern_type == "correction":
        return (
            "When this symptom shows up again, apply this correction before making broader edits "
            "or rerunning the failing command."
        )
    if pattern_type == "success":
        return (
            "Record the exact command sequence or settings that produced this pass so the next "
            "run can mirror them (script, Makefile target, or `memory/` note)."
        )
    return ""


def merge_pattern_types(a: str, b: str) -> str:
    rank = {"error": 4, "lesson": 3, "correction": 2, "success": 2, "general": 0}
    return a if rank.get(a, 0) >= rank.get(b, 0) else b


def merge_suggested_fix(existing: str, new: str) -> str:
    if not existing:
        return new
    if not new:
        return existing
    if new in existing or existing in new:
        return existing if len(existing) >= len(new) else new
    return existing


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


def _category_for_line(line: str, pattern_type: str) -> str:
    if pattern_type == "correction":
        return "correction"
    if pattern_type == "success":
        return "success"
    if pattern_type == "lesson":
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
        ptype = classify_pattern_type(stripped)
        if ptype == "general":
            continue
        text = normalize_insight_text(stripped)
        if not text:
            continue
        fix = suggest_fix_for_line(stripped, ptype)
        sev = _severity_for_line(stripped)
        if ptype == "success":
            sev = "info"
        elif ptype in ("correction", "lesson"):
            sev = "info" if sev == "info" else sev
        yield Insight(
            text=text,
            source_paths=[rel],
            severity=sev,
            category=_category_for_line(stripped, ptype),
            pattern_type=ptype,
            hit_count=1,
            suggested_fix=fix,
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
        elif isinstance(obj, str) and (
            FAILURE_HINTS.search(obj)
            or SUCCESS_HINTS.search(obj)
            or CORRECTION_HINTS.search(obj)
            or LESSON_HINTS.search(obj)
        ):
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
                pattern_type=ins.pattern_type,
                hit_count=ins.hit_count,
                suggested_fix=ins.suggested_fix,
            )
        else:
            existing.hit_count += ins.hit_count
            for p in ins.source_paths:
                if p not in existing.source_paths:
                    existing.source_paths.append(p)
            sev_rank = {"error": 3, "warning": 2, "info": 1}
            if sev_rank.get(ins.severity, 0) > sev_rank.get(existing.severity, 0):
                existing.severity = ins.severity
            existing.pattern_type = merge_pattern_types(existing.pattern_type, ins.pattern_type)
            existing.suggested_fix = merge_suggested_fix(existing.suggested_fix, ins.suggested_fix)
    return list(buckets.values())


def build_recurring_payloads(
    insights: Sequence[Insight], min_hits: int = RECURRING_MIN_HITS
) -> list[dict[str, Any]]:
    """Surface lines that fire more than once across files or repeated lines."""

    out: list[dict[str, Any]] = []
    for ins in insights:
        if ins.hit_count < min_hits:
            continue
        fix = ins.suggested_fix
        if ins.pattern_type == "error" and not fix:
            fix = (
                "Same signature across multiple runs: add a regression test, tighten logging at "
                "the callsite, or add a cheap preflight step so the agent stops before repeating "
                "the failure."
            )
        out.append(
            {
                "pattern_type": ins.pattern_type,
                "text": ins.text,
                "hits": ins.hit_count,
                "sources": ins.source_paths[:8],
                "suggested_fix": fix,
                "severity": ins.severity,
            }
        )
    out.sort(key=lambda x: (-int(x["hits"]), x["pattern_type"], x["text"].lower()))
    return out


def load_error_log_highlights(root: Path) -> list[dict[str, Any]]:
    """Mine `.learnings/error_log.json` for repeated normalized errors (pairs with error_learning.py)."""

    path = root / LEARNINGS_DIR / ERROR_LOG_JSON
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    counts: Counter[str] = Counter()
    sample_by_norm: dict[str, dict[str, Any]] = {}
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        err = str(raw.get("error", "")).strip()
        if len(err) < 8:
            continue
        norm = normalize_insight_text(err)[:220]
        counts[norm] += 1
        sample_by_norm.setdefault(norm, raw)

    out: list[dict[str, Any]] = []
    for norm, cnt in counts.most_common(16):
        if cnt < RECURRING_MIN_HITS:
            continue
        sample = sample_by_norm.get(norm, {})
        lesson = str(sample.get("lesson", "")).strip()
        out.append(
            {
                "normalized_error": norm,
                "occurrences_in_log": cnt,
                "lesson": lesson[:500],
                "resolved": bool(sample.get("resolved", False)),
                "category": str(sample.get("category", "")).strip(),
            }
        )
    return out


def build_summary_markdown(
    run_at: datetime,
    files_scanned: int,
    insights: Sequence[Insight],
    top_sessions: Sequence[str],
    recurring: Sequence[dict[str, Any]] | None = None,
    error_log_highlights: Sequence[dict[str, Any]] | None = None,
) -> str:
    recurring = list(recurring or ())
    error_log_highlights = list(error_log_highlights or ())
    lines = [
        f"# Reflection summary ({run_at.date().isoformat()} UTC)",
        "",
        f"- Session files scanned: **{files_scanned}**",
        f"- Distinct insights: **{len(insights)}**",
        "",
    ]
    if top_sessions:
        lines.append("## Recently touched logs")
        for p in top_sessions[:15]:
            lines.append(f"- `{p}`")
        lines.append("")
    if recurring:
        lines.append("## Recurring session signals")
        lines.append(
            "_Clusters below hit multiple times in the scanned window — treat as backlog items._"
        )
        lines.append("")
        for block in recurring[:18]:
            fix = block.get("suggested_fix") or ""
            lines.append(
                f"- **[{block['pattern_type']}] ×{block['hits']}** {block['text']}"
                + (f"\n  - **Fix:** {fix}" if fix else "")
            )
        lines.append("")
    if error_log_highlights:
        lines.append("## Recurring errors from `error_log.json`")
        for row in error_log_highlights[:12]:
            status = "resolved" if row.get("resolved") else "open"
            cat = row.get("category") or "uncategorized"
            lines.append(
                f"- **[{status}] [{cat}]** ({row['occurrences_in_log']}×) `{row['normalized_error'][:160]}`"
            )
            if row.get("lesson"):
                lines.append(f"  - **Recorded lesson:** {row['lesson'][:280]}")
        lines.append("")
    if not insights:
        if not recurring and not error_log_highlights:
            lines.append("_No notable patterns in the scanned window._")
        return "\n".join(lines).rstrip() + "\n"

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
            hits = f" ×{ins.hit_count}" if ins.hit_count > 1 else ""
            fix = f"\n  - **Suggested:** {ins.suggested_fix}" if ins.suggested_fix else ""
            lines.append(
                f"- **[{badge}] [{ins.pattern_type}]{hits}** {ins.text} _(sources: {sources})_{fix}"
            )
        lines.append("")

    ctr = Counter(i.category for i in insights)
    lines.append("## Category counts")
    for cat, n in ctr.most_common():
        lines.append(f"- **{cat}**: {n}")
    return "\n".join(lines).rstrip() + "\n"


def build_concise_lessons_summary(run: ReflectionRun) -> str:
    """Short stdout digest for humans and cron mail — favors recurring items and fixes."""

    lines: list[str] = [
        f"Lessons ({run.run_id}): scanned {run.files_scanned} file(s); "
        f"{len(run.insights)} distinct signal(s).",
    ]
    for block in run.recurring_from_sessions:
        if len(lines) >= CONCISE_MAX_LINES:
            break
        fix = str(block.get("suggested_fix") or "").strip()
        tag = str(block.get("pattern_type", "?")).upper()
        hits = int(block["hits"])
        text = str(block["text"])
        if len(text) > 118:
            text = text[:115] + "..."
        lines.append(f"- [{tag} ×{hits}] {text}")
        if fix and len(lines) < CONCISE_MAX_LINES:
            lines.append(f"  → {fix}")

    for row in run.error_log_highlights:
        if len(lines) >= CONCISE_MAX_LINES:
            break
        n = int(row["occurrences_in_log"])
        snippet = str(row["normalized_error"])
        if len(snippet) > 100:
            snippet = snippet[:97] + "..."
        lines.append(f"- [ERROR_LOG ×{n}] {snippet}")
        lesson = str(row.get("lesson") or "").strip()
        if lesson and len(lines) < CONCISE_MAX_LINES:
            lines.append(f"  → {lesson[:220]}")

    if len(lines) == 1:
        errs = [i for i in run.insights if i.pattern_type == "error"]
        errs.sort(
            key=lambda x: ({"error": 0, "warning": 1, "info": 2}.get(x.severity, 9), -len(x.text))
        )
        for ins in errs:
            if len(lines) >= CONCISE_MAX_LINES:
                break
            t = ins.text if len(ins.text) <= 120 else ins.text[:117] + "..."
            lines.append(f"- [ERROR] {t}")
            if ins.suggested_fix and len(lines) < CONCISE_MAX_LINES:
                lines.append(f"  → {ins.suggested_fix}")
        for ins in [i for i in run.insights if i.pattern_type == "success"][:2]:
            if len(lines) >= CONCISE_MAX_LINES:
                break
            t = ins.text if len(ins.text) <= 100 else ins.text[:97] + "..."
            lines.append(f"- [SUCCESS] {t}")

    if len(lines) == 1:
        lines.append("- No prioritized signals in this window (tune globs or --since-hours).")

    return "\n".join(lines[:CONCISE_MAX_LINES]) + "\n"


def weekly_report_path(root: Path, dt: datetime) -> Path:
    iso = dt.isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"
    return root / LEARNINGS_DIR / SUMMARIES_SUBDIR / f"weekly_{week}.md"


def update_weekly_summary(root: Path, run_at: datetime, body: str) -> Path:
    """Append this run into the ISO-week summary file (idempotent headings per day)."""

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
        path.write_text(f"# Weekly reflection — {run_at.isocalendar().year} W{run_at.isocalendar().week:02d}\n" + chunk)
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
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    json_path.write_text(json.dumps(asdict(run), indent=2) + "\n", encoding="utf-8")
    return md_path, json_path


def write_recurring_and_action_artifacts(root: Path, run: ReflectionRun) -> tuple[Path, Path]:
    """Persist recurring clusters and a short action list for humans and automation."""

    learn = root / LEARNINGS_DIR
    learn.mkdir(parents=True, exist_ok=True)
    recurring_path = learn / RECURRING_JSON
    recurring_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run.run_id,
                "generated_at_utc": run.finished_at_utc,
                "recurring_from_sessions": run.recurring_from_sessions,
                "error_log_highlights": run.error_log_highlights,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    lines: list[str] = [
        f"# Action items — `{run.run_id}`",
        "",
        f"_UTC {run.finished_at_utc}. Prioritize rows below for playbooks, tests, or guardrails._",
        "",
    ]
    if run.recurring_from_sessions:
        lines.append("## Recurring session clusters")
        lines.append("")
        for block in run.recurring_from_sessions[:25]:
            lines.append(f"### ({block['hits']}×) [{block['pattern_type']}] {block['text'][:260]}")
            lines.append("")
            fix = block.get("suggested_fix") or "—"
            lines.append(f"- **Suggested fix:** {fix}")
            lines.append("")
            srcs = block.get("sources") or []
            if srcs:
                lines.append("- **Seen in:** " + ", ".join(f"`{s}`" for s in srcs[:8]))
                lines.append("")

    if run.error_log_highlights:
        lines.append("## Recurring structured errors (`error_log.json`)")
        lines.append("")
        for row in run.error_log_highlights[:18]:
            lines.append(
                f"- **{row['occurrences_in_log']}×** [{row.get('category') or '—'}] "
                f"`{row['normalized_error'][:200]}`"
            )
            if row.get("lesson"):
                lines.append(f"  - **Apply:** {str(row['lesson'])[:360]}")
            lines.append("")

    if not run.recurring_from_sessions and not run.error_log_highlights:
        lines.append(
            "_No recurring clusters this pass; see `insights/` for the full per-run digest._"
        )
        lines.append("")

    action_path = learn / ACTION_ITEMS_MD
    action_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return recurring_path, action_path


def write_latest_pointers(
    root: Path,
    md_path: Path,
    weekly_path: Path,
    *,
    recurring_json: Path | None = None,
    action_md: Path | None = None,
) -> None:
    """Small files for automation consumers."""

    ptr = root / LEARNINGS_DIR / "latest.json"
    data: dict[str, Any] = {
        "insights_md": md_path.relative_to(root).as_posix(),
        "weekly_summary_md": weekly_path.relative_to(root).as_posix(),
        "generated_at_utc": utc_now().replace(microsecond=0).isoformat(),
    }
    if recurring_json is not None:
        data["recurring_mistakes_json"] = recurring_json.relative_to(root).as_posix()
    if action_md is not None:
        data["action_items_md"] = action_md.relative_to(root).as_posix()
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

    insights: list[Insight] = []
    for sf in session_files:
        insights.extend(read_and_extract(sf, root))

    insights = dedupe_insights(insights)
    recurring = build_recurring_payloads(insights)
    error_log_highlights = load_error_log_highlights(root)
    insights.sort(key=lambda x: (x.category, x.severity, x.text))

    top_sessions = [p.relative_to(root).as_posix() for p in session_files[:20]]
    summary = build_summary_markdown(
        started,
        len(session_files),
        insights,
        top_sessions,
        recurring,
        error_log_highlights,
    )

    finished = utc_now()
    run_id = started.strftime("%Y%m%d_%H%M%S")

    run = ReflectionRun(
        run_id=run_id,
        started_at_utc=started.replace(microsecond=0).isoformat(),
        finished_at_utc=finished.replace(microsecond=0).isoformat(),
        files_scanned=len(session_files),
        session_files=[p.relative_to(root).as_posix() for p in session_files],
        insights=insights,
        summary_markdown=summary,
        recurring_from_sessions=recurring,
        error_log_highlights=error_log_highlights,
    )

    if dry_run:
        return run

    md_path, _ = write_insight_artifacts(root, run)
    recurring_path, action_path = write_recurring_and_action_artifacts(root, run)
    weekly_path = update_weekly_summary(root, started, summary)
    write_latest_pointers(
        root,
        md_path,
        weekly_path,
        recurring_json=recurring_path,
        action_md=action_path,
    )

    state["last_run_utc"] = finished.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state["last_run_id"] = run_id
    state["last_insight_count"] = len(insights)
    save_state(root, state)

    return run


def maybe_post_results(run: ReflectionRun, *, dry_run: bool) -> list[str]:
    log: list[str] = []
    text = run.summary_markdown
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
            "recurring_session_clusters": len(run.recurring_from_sessions),
            "error_log_clusters": len(run.error_log_highlights),
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
        help="Print the full markdown summary to stdout (default is a short lessons digest).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stdout (keep stderr notices); useful for cron when mail is unwanted.",
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

    if not args.quiet:
        if args.stdout_summary:
            print(run.summary_markdown)
        else:
            print(build_concise_lessons_summary(run), end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())