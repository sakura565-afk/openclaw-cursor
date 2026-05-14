#!/usr/bin/env python3
"""
Periodic self-reflection over recent agent sessions and logs (cron-friendly).

Scans the workspace plus OpenClaw session trees (``.openclaw/sessions/``,
``~/.openclaw/sessions/``), derives wins, losses, and heuristic session metrics,
and writes a concise daily digest under ``memory/.learnings/YYYY-MM-DD.md``.

CLI::

    python scripts/auto_reflection.py run [--days 7] [--dry-run] [--root PATH]

Machine-readable summary (stdout, one line)::

    AUTO_REFLECTION_V1 {\"sessions_processed\": ...}

Human-oriented status lines go to stderr. Optional posting via
``REFLECTION_WEBHOOK_URL`` or Telegram env vars (see bottom of this docstring).

Environment (optional)::

    AUTO_REFLECTION_ROOT — workspace root (default: cwd)
    AUTO_REFLECTION_SESSION_GLOBS — extra comma-separated globs under each root
    AUTO_REFLECTION_SESSION_DIRS — extra comma-separated directories to scan
    OPENCLAW_SESSIONS_DIR — explicit OpenClaw sessions directory (overrides defaults)
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import logging
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

log = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Layout
# -----------------------------------------------------------------------------

MEMORY_LEARNINGS_SUBDIR = Path("memory") / ".learnings"
STATE_FILENAME = ".state.json"
LATEST_JSON = "latest.json"

DEFAULT_DAYS = 7
DEFAULT_OVERLAP_MINUTES = 90
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SCANNED_FILES = 500
TELEGRAM_TEXT_LIMIT = 4000
MACHINE_PREFIX = "AUTO_REFLECTION_V1"

DEFAULT_SESSION_GLOBS: tuple[str, ...] = (
    "logs/**/*.log",
    "logs/**/*.json",
    "memory/**/*_log.md",
    "memory/**/*.md",
    "memory/**/conversation_extract_*.json",
    "**/session.json",
    ".openclaw/sessions/**/*.json",
    ".openclaw/sessions/**/*.md",
    ".openclaw/sessions/**/*.jsonl",
)

FAILURE_HINTS = re.compile(
    r"(?i)(\b(traceback|exception|error|failed|failure|timed?\s*out|exit\s*code\s*[1-9]\d*)\b|"
    r"\b(Fatal|Critical)\b|^Error:|\[\s*ERROR\s*\])",
)
LESSON_HINTS = re.compile(
    r"(?i)(\blesson learned\b|\btakeaway\b|\bremember to\b|\bnext time\b|"
    r"\bavoid\b|\bshould have\b|\broot cause\b)",
)
WIN_HINTS = re.compile(
    r"(?i)(\b(fixed|resolved|completed|success(?:ful)?|passed|passing|"
    r"all\s+tests\s+pass|tests?\s+pass|green\b|shipped|merged|deployed|"
    r"works\s+now|verified|unblocked|achievement)\b|✅|🎉)",
)
LOSS_EXTRA = re.compile(
    r"(?i)(\b(regression|blocked|rollback|revert(?:ed)?|"
    r"tests?\s+fail|ci\s+fail|build\s+fail|incident|"
    r"root\s+cause\s*:\s*failure)\b)",
)

COMPLETION_HINT = re.compile(
    r"(?i)\b(completed|done|resolved|fixed|success|passed|shipped|merged|green build|all tests pass)\b",
)
BLOCKER_HINT = re.compile(
    r"(?i)\b(failed|failure|blocked|stuck|unable to|cannot\b|timeout|error:|traceback)\b",
)

AGENTS_SECTION_HEADING = re.compile(r"(?mi)^##\s+.*(learnings|\.learnings).*$")


@dataclass
class Insight:
    """One deduplicated insight line derived from logs or transcripts."""

    text: str
    source_paths: list[str] = field(default_factory=list)
    severity: str = "info"  # info | warning | error
    category: str = "general"


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

    @property
    def context_switches_per_100_segments(self) -> float | None:
        if self.segment_rows == 0:
            return None
        return 100.0 * self.context_switches / self.segment_rows


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


@dataclass
class ReflectionRun:
    """Serializable result of one reflection pass."""

    run_id: str
    started_at_utc: str
    finished_at_utc: str
    sessions_processed: int
    session_files: list[str]
    insights: list[Insight]
    summary_markdown: str
    daily_markdown_path: str
    aggregated_metrics: dict[str, Any] = field(default_factory=dict)
    errors_encountered: list[str] = field(default_factory=list)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parent.parent


def ensure_repo_on_path() -> Path:
    root = repo_root_from_script()
    s = str(root)
    if s not in sys.path:
        sys.path.insert(0, s)
    return root


def learnings_dir(root: Path) -> Path:
    return root / MEMORY_LEARNINGS_SUBDIR


def state_path(root: Path) -> Path:
    return learnings_dir(root) / STATE_FILENAME


def load_state(root: Path) -> dict[str, Any]:
    path = state_path(root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not load reflection state: %s", exc)
        return {}


def save_state(root: Path, data: dict[str, Any]) -> None:
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def session_dirs_from_env() -> tuple[Path, ...]:
    raw = os.environ.get("AUTO_REFLECTION_SESSION_DIRS", "")
    roots: list[Path] = []
    for part in raw.split(","):
        p = Path(part.strip()).expanduser()
        if p.is_dir():
            roots.append(p.resolve())
    return tuple(dict.fromkeys(roots))


def openclaw_session_roots(root: Path) -> list[Path]:
    """Default OpenClaw session directories (repo-local and user home)."""

    out: list[Path] = []
    explicit = os.environ.get("OPENCLAW_SESSIONS_DIR", "").strip()
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if p.is_dir():
            out.append(p)
    for candidate in (
        root / ".openclaw" / "sessions",
        Path.home() / ".openclaw" / "sessions",
    ):
        if candidate.is_dir():
            rp = candidate.resolve()
            if rp not in out:
                out.append(rp)
    return out


def rel_under_root(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def is_under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _norm_role(role: str | None) -> str:
    if not role:
        return "unknown"
    r = role.strip().lower()
    mapping = {"human": "user", "agent": "assistant"}
    return mapping.get(r, r)


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


def collect_globs(extra: Sequence[str]) -> tuple[str, ...]:
    merged = list(DEFAULT_SESSION_GLOBS)
    env_extra = os.environ.get("AUTO_REFLECTION_SESSION_GLOBS", "")
    if env_extra.strip():
        merged.extend(p.strip() for p in env_extra.split(",") if p.strip())
    merged.extend(extra)
    return tuple(dict.fromkeys(merged))


def iter_session_files(
    root: Path,
    globs: Sequence[str],
    cutoff: datetime,
    *,
    extra_roots: Sequence[Path] | None = None,
    openclaw_roots: Sequence[Path] | None = None,
) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    bases: list[Path] = [root]
    if extra_roots:
        bases.extend(Path(p).resolve() for p in extra_roots if p.is_dir())

    for base in bases:
        for pattern in globs:
            for path in base.glob(pattern):
                _maybe_add_session_file(path, cutoff, seen, out)

    if openclaw_roots:
        allowed_suffixes = {".json", ".md", ".jsonl", ".txt", ".log"}
        for oc_root in openclaw_roots:
            if not oc_root.is_dir():
                continue
            try:
                for path in oc_root.rglob("*"):
                    if not path.is_file():
                        continue
                    suf = path.suffix.lower()
                    if suf not in allowed_suffixes:
                        continue
                    _maybe_add_session_file(path, cutoff, seen, out)
            except OSError as exc:
                log.warning("OpenClaw scan failed under %s: %s", oc_root, exc)

    def sort_key(p: Path) -> tuple[int, float]:
        try:
            mt = p.stat().st_mtime
        except OSError:
            mt = 0.0
        return (0 if is_under_root(p, root) else 1, -mt)

    out.sort(key=sort_key)
    if len(out) > MAX_SCANNED_FILES:
        return out[:MAX_SCANNED_FILES]
    return out


def _maybe_add_session_file(path: Path, cutoff: datetime, seen: set[Path], out: list[Path]) -> None:
    if not path.is_file():
        return
    try:
        st = path.stat()
    except OSError:
        return
    if datetime.fromtimestamp(st.st_mtime, tz=timezone.utc) < cutoff:
        return
    if st.st_size > MAX_FILE_BYTES:
        return
    rp = path.resolve()
    if rp in seen:
        return
    seen.add(rp)
    out.append(path)


def _severity_for_line(line: str) -> str:
    if re.search(r"(?i)\b(traceback|exception|fatal|critical)\b", line):
        return "error"
    if FAILURE_HINTS.search(line):
        return "warning"
    return "info"


def _category_for_line(line: str) -> str:
    if FAILURE_HINTS.search(line) or LOSS_EXTRA.search(line):
        if LESSON_HINTS.search(line):
            return "lesson"
        return "loss"
    if WIN_HINTS.search(line):
        return "win"
    if LESSON_HINTS.search(line):
        return "lesson"
    if re.search(r"(?i)\b(test|pytest|unittest)\b", line):
        return "testing"
    if re.search(r"(?i)\b(git|commit|merge|branch)\b", line):
        return "git"
    if re.search(r"(?i)\b(api|http|request|timeout)\b", line):
        return "integration"
    return "general"


@functools.lru_cache(maxsize=1)
def _session_parser_pair() -> tuple[Any, Any] | None:
    try:
        from scripts.conversation_extractor import analyze_segments, parse_session_log

        return parse_session_log, analyze_segments
    except ImportError:
        try:
            from conversation_extractor import analyze_segments, parse_session_log

            return parse_session_log, analyze_segments
        except ImportError:
            return None


def _get_parse_session_log() -> Any | None:
    try:
        from scripts.conversation_extractor import parse_session_log

        return parse_session_log
    except ImportError:
        try:
            from conversation_extractor import parse_session_log

            return parse_session_log
        except ImportError:
            return None


def normalize_insight_text(line: str) -> str:
    line = line.strip()
    line = re.sub(r"\s+", " ", line)
    return line[:500]


def insight_fingerprint(text: str) -> str:
    return hashlib.sha256(text.lower().encode("utf-8")).hexdigest()[:16]


def extract_insights_from_openclaw_session(path: Path, root: Path) -> list[Insight] | None:
    pair = _session_parser_pair()
    if pair is None:
        return None
    parse_session_log, analyze_segments = pair
    segments = parse_session_log(path)
    if not segments:
        return None

    rel = rel_under_root(path, root)
    digest = analyze_segments(segments, rel)
    out: list[Insight] = []

    for d in digest.decisions:
        text = normalize_insight_text(d)
        if text:
            out.append(Insight(text=text, source_paths=[rel], severity="info", category="win"))

    for item in digest.learnings:
        text = normalize_insight_text(item)
        if text:
            out.append(Insight(text=text, source_paths=[rel], severity="info", category="lesson"))

    for _turn, _role, text in segments:
        out.extend(_insights_from_raw_text(rel, text))

    if not out:
        return None
    return out


def _insights_from_raw_text(rel: str, raw: str) -> list[Insight]:
    found: list[Insight] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if len(stripped) < 12:
            continue
        if not (
            FAILURE_HINTS.search(stripped)
            or LESSON_HINTS.search(stripped)
            or LOSS_EXTRA.search(stripped)
            or (WIN_HINTS.search(stripped) and len(stripped) >= 16)
        ):
            continue
        norm = normalize_insight_text(stripped)
        if not norm:
            continue
        found.append(
            Insight(
                text=norm,
                source_paths=[rel],
                severity=_severity_for_line(stripped),
                category=_category_for_line(stripped),
            ),
        )
    return found


def extract_insights_from_text(path: Path, root: Path, raw: str) -> Iterator[Insight]:
    rel = rel_under_root(path, root)
    yield from _insights_from_raw_text(rel, raw)


def extract_insights_from_json(path: Path, root: Path, raw: str) -> Iterator[Insight]:
    rel = rel_under_root(path, root)
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


def read_and_extract(path: Path, root: Path, errors: list[str]) -> list[Insight]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        errors.append(f"{rel_under_root(path, root)}: read failed: {exc}")
        return []
    if path.suffix.lower() == ".json":
        structured = extract_insights_from_openclaw_session(path, root)
        if structured is not None:
            return structured
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
            cat_pri = {
                "loss": 5,
                "lesson": 4,
                "win": 3,
                "testing": 2,
                "integration": 2,
                "git": 2,
                "general": 1,
            }
            if cat_pri.get(ins.category, 0) > cat_pri.get(existing.category, 0):
                existing.category = ins.category
    return list(buckets.values())


def collect_per_file_metrics(root: Path, session_files: list[Path], errors: list[str]) -> list[SessionMetrics]:
    rows: list[SessionMetrics] = []
    parse_session_log = _get_parse_session_log()
    for sf in session_files:
        rel = rel_under_root(sf, root)
        try:
            raw = sf.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            errors.append(f"{rel}: read failed: {exc}")
            continue
        segments: list[tuple[int, str | None, str]] = []
        if parse_session_log is not None:
            try:
                segments = parse_session_log(sf)
            except Exception as exc:  # noqa: BLE001 — best-effort metrics
                errors.append(f"{rel}: parse_session_log: {exc}")
                segments = []
        if len(segments) >= 2:
            rows.append(metrics_from_segments(rel, segments))
        elif raw.strip():
            rows.append(_metrics_from_plain_text(rel, raw))
    return rows


def build_went_well_and_improve(
    agg: AggregatedSessionMetrics,
    insights: Sequence[Insight],
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
                f"(estimated task signal rate **{tcr:.0%}**).",
            )
        elif tcr is not None and tcr < 0.45:
            improve.append(
                f"Friction markers were common versus completion language "
                f"(estimated task signal rate **{tcr:.0%}**); tighten error handling or scope.",
            )

        if tsr is not None and tsr >= 0.82 and agg.tool_output_rows >= 3:
            went_well.append(
                f"Tool outputs were mostly clean versus error heuristics "
                f"(**{tsr:.0%}** success estimate over **{agg.tool_output_rows}** tool-result rows).",
            )
        elif tsr is not None and tsr < 0.55 and agg.tool_output_rows >= 3:
            improve.append(
                f"Tool-result rows skew toward failures/timeouts in heuristics "
                f"(**{tsr:.0%}** estimated success); review flaky tools or arguments.",
            )

        if sw_rate is not None and sw_rate <= 35:
            went_well.append(
                f"Role flow stayed relatively steady (**{sw_rate:.1f}** context switches per 100 segments).",
            )
        elif sw_rate is not None and sw_rate >= 70:
            improve.append(
                f"High role churn (**{sw_rate:.1f}** switches per 100 segments); batch tool work "
                f"and reduce assistant/user ping-pong where possible.",
            )

    if err_like == 0 and warn_like <= 2 and len(insights) > 0:
        went_well.append("Log scan surfaced lessons or info-level notes with few hard errors.")
    if err_like >= 3:
        improve.append(f"Several error-class log lines deduped into **{err_like}** distinct error insights.")
    if warn_like >= 6:
        improve.append(
            f"Elevated warning-style signals (**{warn_like}** insights); consider addressing recurring warnings first.",
        )

    if not went_well:
        went_well.append("Maintain the habit of capturing explicit decisions and lessons in session logs.")
    if not improve:
        improve.append("Keep scanning for slow tests, integration timeouts, and ambiguous requirements early.")

    return went_well, improve


def extract_agents_learnings_bullets(agents_text: str) -> tuple[str | None, list[str]]:
    """Return (heading line, bullets) for the first AGENTS.md learnings-like section."""

    m = AGENTS_SECTION_HEADING.search(agents_text)
    if not m:
        return None, []
    start = m.start()
    rest = agents_text[start:]
    lines = rest.splitlines()
    if not lines:
        return None, []
    heading = lines[0].strip()
    bullets: list[str] = []
    for line in lines[1:]:
        if re.match(r"^##\s+", line):
            break
        stripped = line.strip()
        if stripped.startswith(("- ", "* ", "• ")):
            bullets.append(stripped.lstrip("-*• ").strip())
        elif re.match(r"^\d+\.\s+", stripped):
            bullets.append(re.sub(r"^\d+\.\s+", "", stripped).strip())
    return heading, bullets[:25]


def read_agents_context(root: Path) -> tuple[str | None, list[str]]:
    path = root / "AGENTS.md"
    if not path.is_file():
        return None, []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, []
    return extract_agents_learnings_bullets(text)


def build_daily_markdown(
    run_at: datetime,
    run_id: str,
    agg: AggregatedSessionMetrics,
    went_well: Sequence[str],
    improve: Sequence[str],
    insights: Sequence[Insight],
    agents_heading: str | None,
    agents_bullets: Sequence[str],
    top_sessions: Sequence[str],
    errors: Sequence[str],
) -> str:
    date_s = run_at.date().isoformat()
    lines = [
        f"# Self-reflection — {date_s} (UTC)",
        "",
        f"_Run `{run_id}` — auto-generated by `scripts/auto_reflection.py`._",
        "",
        "## Metrics",
        "",
        f"- **Sessions / files processed**: {agg.files_parsed}",
        f"- **Segment rows**: {agg.segment_rows}",
        f"- **Context switches** (role transitions): **{agg.context_switches}**",
    ]
    csr = agg.context_switches_per_100_segments
    lines.append(
        f"- **Context switches / 100 segments**: **{csr:.1f}**"
        if csr is not None
        else "- **Context switches / 100 segments**: _n/a_",
    )
    lines.extend(
        [
            f"- **Tool invocations (structured)**: {agg.tool_invocations}",
            f"- **Tool output rows**: {agg.tool_output_rows}",
            f"- **Tool success (heuristic)**: {agg.tool_success_estimated}",
            f"- **Tool failure (heuristic)**: {agg.tool_failure_estimated}",
        ],
    )
    tcr = agg.task_completion_rate
    tsr = agg.tool_success_rate
    lines.append(
        f"- **Task completion rate (heuristic)**: **{tcr:.0%}**"
        if tcr is not None
        else "- **Task completion rate (heuristic)**: _insufficient completion/friction signals_",
    )
    lines.append(
        f"- **Tool success rate (heuristic)**: **{tsr:.0%}**"
        if tsr is not None
        else "- **Tool success rate (heuristic)**: _no tool-result rows parsed_",
    )

    if top_sessions:
        lines.extend(["", "## Recently touched session sources", ""])
        for p in top_sessions[:20]:
            lines.append(f"- `{p}`")

    if agents_heading and agents_bullets:
        lines.extend(
            [
                "",
                "## Guidance from AGENTS.md (learnings section)",
                "",
                f"_Section: {agents_heading}_",
                "",
            ],
        )
        for b in agents_bullets:
            if b:
                lines.append(f"- {b}")
    elif agents_heading:
        lines.extend(["", "## Guidance from AGENTS.md", "", f"_Section: {agents_heading} (no bullets parsed)._", ""])

    lines.extend(["", "## What went well", ""])
    for w in went_well:
        lines.append(f"- {w}")

    lines.extend(["", "## What went wrong / friction", ""])
    for x in improve:
        lines.append(f"- {x}")

    rank = {"error": 0, "warning": 1, "info": 2}
    actionable = sorted(insights, key=lambda i: (rank.get(i.severity, 9), -len(i.source_paths)))[:20]
    lines.extend(["", "## Actionable insights", ""])
    if actionable:
        for ins in actionable:
            lines.append(f"- **[{ins.severity.upper()}][{ins.category}]** {ins.text}")
    else:
        lines.append("_No high-signal lines in this window._")

    wins = [i for i in insights if i.category == "win"][:12]
    losses = [i for i in insights if i.category == "loss"][:12]
    if wins:
        lines.extend(["", "## Win highlights (from transcripts)", ""])
        for ins in wins:
            lines.append(f"- {ins.text}")
    if losses:
        lines.extend(["", "## Loss / risk highlights", ""])
        for ins in losses:
            lines.append(f"- **[{ins.severity.upper()}]** {ins.text}")

    if errors:
        lines.extend(["", "## Errors encountered while scanning", ""])
        for e in errors[:30]:
            lines.append(f"- `{e}`")

    lines.extend(
        [
            "",
            "---",
            "",
            "This file complements any **`.learnings/`** notes in `AGENTS.md`: keep both in sync manually when you adopt new team conventions.",
            "",
        ],
    )
    return "\n".join(lines).rstrip() + "\n"


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
        f"- Task completion rate: **{tcr:.0%}**" if tcr is not None else "- Task completion rate: _n/a_",
    )
    lines.append(
        f"- Tool success rate: **{tsr:.0%}**" if tsr is not None else "- Tool success rate: _n/a_",
    )
    lines.append(
        f"- Context switches / 100 segments: **{csr:.1f}**"
        if csr is not None
        else "- Context switches / 100 segments: _n/a_",
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
        )[:25]:
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


def write_latest_pointers(root: Path, daily_rel: str) -> None:
    ptr = learnings_dir(root) / LATEST_JSON
    ptr.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "daily_markdown": daily_rel,
        "generated_at_utc": utc_now().replace(microsecond=0).isoformat(),
    }
    ptr.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def machine_summary_payload(
    run: ReflectionRun,
    *,
    dry_run: bool,
    daily_written: bool,
) -> dict[str, Any]:
    key_insights = [
        i.text
        for i in sorted(
            run.insights,
            key=lambda x: ({"error": 0, "warning": 1, "info": 2}.get(x.severity, 9), -len(x.text)),
        )[:15]
    ]
    return {
        "schema": "auto_reflection_v1",
        "ok": True,
        "dry_run": dry_run,
        "run_id": run.run_id,
        "started_at_utc": run.started_at_utc,
        "finished_at_utc": run.finished_at_utc,
        "sessions_processed": run.sessions_processed,
        "insights_distinct": len(run.insights),
        "key_insights": key_insights,
        "errors_encountered": run.errors_encountered,
        "daily_markdown_path": run.daily_markdown_path,
        "daily_markdown_written": daily_written,
        "aggregated_metrics": run.aggregated_metrics,
    }


def emit_machine_summary(payload: dict[str, Any]) -> None:
    line = f"{MACHINE_PREFIX} {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    print(line, file=sys.stdout, flush=True)


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
            {"chat_id": chat_id, "text": prefix + chunk, "disable_web_page_preview": True},
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


def maybe_post_results(run: ReflectionRun, *, dry_run: bool) -> list[str]:
    log_lines: list[str] = []
    text = run.summary_markdown
    webhook = os.environ.get("REFLECTION_WEBHOOK_URL", "").strip()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    payload = {
        "text": text,
        "meta": {
            "run_id": run.run_id,
            "started_at": run.started_at_utc,
            "sessions_processed": run.sessions_processed,
            "insight_count": len(run.insights),
            "metrics": run.aggregated_metrics,
        },
    }

    if dry_run:
        log_lines.append("[dry-run] Skipping webhook and Telegram.")
        return log_lines

    if webhook:
        ok, msg = post_webhook(webhook, payload)
        log_lines.append(f"Webhook {'ok' if ok else 'FAILED'}: {msg[:400]}")

    if token and chat_id:
        ok, msg = post_telegram_summary(token, chat_id, text)
        log_lines.append(f"Telegram {'ok' if ok else 'FAILED'}: {msg[:400]}")
    elif token or chat_id:
        log_lines.append("Telegram skipped: need both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")

    if not webhook and not (token and chat_id):
        log_lines.append("No REFLECTION_WEBHOOK_URL or full Telegram credentials; summary only on disk.")

    return log_lines


def run_reflection(
    root: Path,
    *,
    days: float,
    extra_globs: Sequence[str],
    overlap_minutes: int = DEFAULT_OVERLAP_MINUTES,
    dry_run: bool = False,
) -> ReflectionRun:
    ensure_repo_on_path()
    started = utc_now()
    since_hours = max(days, 0.01) * 24.0
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
    oc_roots = openclaw_session_roots(root)
    session_files = iter_session_files(
        root,
        globs,
        cutoff,
        extra_roots=session_dirs_from_env(),
        openclaw_roots=oc_roots,
    )

    errors: list[str] = []
    per_file = collect_per_file_metrics(root, session_files, errors)
    agg = aggregate_session_metrics(per_file)

    insights: list[Insight] = []
    for sf in session_files:
        insights.extend(read_and_extract(sf, root, errors))

    insights = dedupe_insights(insights)
    insights.sort(key=lambda x: (x.category, x.severity, x.text))

    went_well, improve = build_went_well_and_improve(agg, insights)
    agents_heading, agents_bullets = read_agents_context(root)

    top_sessions = [rel_under_root(p, root) for p in session_files[:20]]
    summary = build_summary_markdown(started, len(session_files), insights, top_sessions, agg)

    run_id = started.strftime("%Y%m%d_%H%M%S")
    daily_body = build_daily_markdown(
        started,
        run_id,
        agg,
        went_well,
        improve,
        insights,
        agents_heading,
        agents_bullets,
        top_sessions,
        errors,
    )

    finished = utc_now()
    day_name = f"{started.date().isoformat()}.md"
    daily_path = learnings_dir(root) / day_name
    daily_rel = daily_path.relative_to(root).as_posix()

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
        sessions_processed=len(session_files),
        session_files=[rel_under_root(p, root) for p in session_files],
        insights=insights,
        summary_markdown=summary,
        daily_markdown_path=daily_rel,
        aggregated_metrics=agg_dict,
        errors_encountered=list(errors),
    )

    if dry_run:
        return run

    daily_path.parent.mkdir(parents=True, exist_ok=True)
    daily_path.write_text(daily_body, encoding="utf-8")
    write_latest_pointers(root, daily_rel)

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
                "daily_markdown_path": run.daily_markdown_path,
                "run_id": run.run_id,
                "sessions_processed": run.sessions_processed,
                "insight_count": len(run.insights),
                "metrics": run.aggregated_metrics,
            },
        )
    except Exception:  # noqa: BLE001 — optional coordination hook
        pass

    return run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Periodic self-reflection over sessions and logs; writes memory/.learnings/YYYY-MM-DD.md.",
    )
    sub = parser.add_subparsers(dest="command", help="Command to run")

    run_p = sub.add_parser("run", help="Scan recent sessions and refresh learnings.")
    run_p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Workspace root (default: AUTO_REFLECTION_ROOT or cwd).",
    )
    run_p.add_argument(
        "--days",
        type=float,
        default=DEFAULT_DAYS,
        help=f"Rolling window in days when no prior run state exists (default: {DEFAULT_DAYS}).",
    )
    run_p.add_argument(
        "--glob",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Additional glob relative to root (repeatable).",
    )
    run_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write files, update state, or POST; still prints machine summary.",
    )
    run_p.add_argument(
        "--stdout-summary",
        action="store_true",
        help="Also print the long markdown summary to stdout after the machine line.",
    )
    return parser


def _normalize_argv(argv: list[str] | None) -> list[str]:
    """Support legacy invocations without the ``run`` subcommand."""

    if argv is None:
        return sys.argv[1:]
    if not argv:
        return []
    first = argv[0]
    legacy_starts = ("--root", "--days", "--since-hours", "--dry-run", "--stdout-summary", "--glob", "-h", "--help")
    if first not in ("run",) and (first in legacy_starts or first.startswith("-")):
        out = ["run"]
        i = 0
        while i < len(argv):
            if argv[i] == "--since-hours" and i + 1 < len(argv):
                try:
                    hours = float(argv[i + 1])
                    days = max(hours / 24.0, 0.01)
                    out.extend(["--days", str(days)])
                except ValueError:
                    out.extend(argv[i : i + 2])
                i += 2
                continue
            out.append(argv[i])
            i += 1
        return out
    return list(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    argv_eff = _normalize_argv(argv)
    parser = build_parser()
    args = parser.parse_args(argv_eff)

    if not getattr(args, "command", None):
        parser.print_help(file=sys.stderr)
        print("", file=sys.stderr)
        print("Hint: use `python scripts/auto_reflection.py run --days 7`", file=sys.stderr)
        return 2

    if args.command != "run":
        return 2

    root = args.root or Path(os.environ.get("AUTO_REFLECTION_ROOT", "") or ".").resolve()
    if args.dry_run:
        print(f"[dry-run] root={root}", file=sys.stderr)

    run = run_reflection(
        root,
        days=args.days,
        extra_globs=args.glob,
        dry_run=args.dry_run,
    )

    daily_written = not args.dry_run and (root / run.daily_markdown_path).is_file()
    emit_machine_summary(machine_summary_payload(run, dry_run=args.dry_run, daily_written=daily_written))

    for line in maybe_post_results(run, dry_run=args.dry_run):
        print(line, file=sys.stderr)

    if args.stdout_summary:
        print(run.summary_markdown, file=sys.stdout)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
