#!/usr/bin/env python3
"""
Daily self-reflection over `memory/`: error/correction patterns, daily note, promoted learnings.

Scans markdown under ``memory/`` modified in the last 7 days (excluding this run's daily output
file), derives insights, writes ``memory/YYYY-MM-DD.md`` (UTC date) inside HTML comment
markers so manual notes can coexist, and appends high-value items to ``.learnings/LEARNINGS.md``.

Cron (daily 09:00 UTC), from repo root::

    0 9 * * * cd /path/to/repo && /usr/bin/python3 -m scripts.self_improvement.auto_reflection

Environment (optional):

- ``AUTO_REFLECTION_ROOT`` — workspace root (default: current working directory)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from scripts.auto_reflection import (
    FAILURE_HINTS,
    Insight,
    dedupe_insights,
    insight_fingerprint,
    read_and_extract,
    utc_now,
)

MEMORY_DIR = "memory"
LEARNINGS_DIR = ".learnings"
LEARNINGS_FILE = "LEARNINGS.md"
MARKER_START = "<!-- auto_reflection:self_improvement:start -->"
MARKER_END = "<!-- auto_reflection:self_improvement:end -->"
DEFAULT_LOOKBACK_DAYS = 7
MAX_FILE_BYTES = 2 * 1024 * 1024

CORRECTION_HINTS = re.compile(
    r"(?i)(\b(fixed|fix|corrected|correction|resolved|resolution|workaround|patch|"
    r"mitigation|solution|root cause addressed|updated to|changed to|now uses)\b|"
    r"\b(issue was|turned out|actually needed)\b)"
)


def _root_from_env(cli_root: Path | None) -> Path:
    return (cli_root or Path(os.environ.get("AUTO_REFLECTION_ROOT", "") or ".")).resolve()


def _daily_note_path(root: Path, when: datetime) -> Path:
    return root / MEMORY_DIR / f"{when.date().isoformat()}.md"


def _iter_memory_markdown(root: Path, cutoff: datetime, exclude: Path) -> list[Path]:
    mem = root / MEMORY_DIR
    if not mem.is_dir():
        return []
    out: list[Path] = []
    for path in mem.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            if path.resolve() == exclude.resolve():
                continue
        except OSError:
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        if st.st_size > MAX_FILE_BYTES:
            continue
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            continue
        out.append(path)
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def _benign_stats_failure_line(line: str) -> bool:
    """True when ``failed`` is only a zero counter inside a stats blob."""

    s = line.strip()
    if re.search(r"['\"]failed['\"]\s*:\s*0\b", s) and ("processed" in s or "stats" in s.lower()):
        return True
    return False


def _line_kind(line: str) -> str | None:
    s = line.strip()
    if len(s) < 12:
        return None
    if FAILURE_HINTS.search(s) and not _benign_stats_failure_line(s):
        return "error"
    if CORRECTION_HINTS.search(s):
        return "correction"
    return None


def _normalize_snippet(line: str) -> str:
    s = re.sub(r"\s+", " ", line.strip())
    return s[:400]


def _extract_error_correction_snippets(path: Path, root: Path) -> tuple[list[str], list[str]]:
    rel = path.relative_to(root).as_posix()
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], []
    errors: list[str] = []
    corrections: list[str] = []
    for line in raw.splitlines():
        kind = _line_kind(line)
        if kind == "error":
            errors.append(f"{rel}: {_normalize_snippet(line)}")
        elif kind == "correction":
            corrections.append(f"{rel}: {_normalize_snippet(line)}")
    return errors, corrections


def _insights_from_memory_files(paths: Sequence[Path], root: Path) -> list[Insight]:
    collected: list[Insight] = []
    for p in paths:
        collected.extend(read_and_extract(p, root))
    deduped = dedupe_insights(collected)
    return [i for i in deduped if not _benign_stats_insight(i)]


def _benign_stats_insight(ins: Insight) -> bool:
    t = ins.text
    if "'failed'" in t and "skipped" in t and "processed" in t:
        return True
    if "stats=" in t.replace(" ", "") and "'failed'" in t:
        return True
    return False


def _pattern_clusters(insights: Sequence[Insight], max_labels: int = 8) -> list[str]:
    """Lightweight pseudo-clusters from category + keyword stems."""

    buckets: Counter[str] = Counter()
    for ins in insights:
        buckets[ins.category] += 1
    lines = [f"{cat} ({n} insight{'s' if n != 1 else ''})" for cat, n in buckets.most_common(max_labels)]

    wordish = re.compile(r"[a-z]{4,}", re.I)
    word_counts: Counter[str] = Counter()
    for ins in insights:
        for w in wordish.findall(ins.text.lower()):
            if w in {"error", "failed", "timeout", "exception", "traceback", "warning"}:
                word_counts[w] += 1
    for w, n in word_counts.most_common(6):
        if n >= 2:
            lines.append(f"recurring term “{w}” ({n} mentions across insights)")
    return lines


def _nearby_error_correction_pairs(raw: str, window: int = 12) -> int:
    lines = raw.splitlines()
    kinds = [_line_kind(ln) for ln in lines]
    pairs = 0
    for i, k in enumerate(kinds):
        if k != "error":
            continue
        lo = max(0, i - window)
        hi = min(len(lines), i + window + 1)
        if "correction" in kinds[lo:hi]:
            pairs += 1
    return pairs


def _pair_count_for_files(paths: Sequence[Path]) -> int:
    total = 0
    for p in paths:
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += _nearby_error_correction_pairs(raw)
    return total


def _suggested_actions(
    insights: Sequence[Insight],
    error_snippets: Sequence[str],
    correction_snippets: Sequence[str],
) -> list[str]:
    actions: list[str] = []
    joined = " ".join(s.lower() for s in error_snippets)

    if re.search(r"timeout|timed out", joined):
        actions.append("Harden time-sensitive calls: add retries, extend deadlines, or surface early warnings when timeouts recur.")
    if re.search(r"traceback|exception", joined):
        actions.append("Add regression coverage or a minimal repro for the dominant exception paths surfaced in memory logs.")
    if len(correction_snippets) >= 3 and len(error_snippets) >= 3:
        actions.append("Codify the most common corrections into a short runbook or checklist so the next session starts from known-good steps.")
    lessonish = [i for i in insights if i.category == "lesson" or "lesson" in i.text.lower()]
    if lessonish:
        actions.append(f"Revisit {len(lessonish)} lesson-oriented insight(s) and turn the top one into a durable rule or automation.")

    if not actions:
        actions.append("Continue logging errors and fixes in `memory/` with explicit wording so future passes can link causes to resolutions.")

    return actions[:6]


def _insight_text_from_learnings_bullet(line: str) -> str | None:
    """Parse `- [date] [SEVERITY/category] body` bullets written by this tool."""

    m = re.match(r"-\s+\[[^\]]+\]\s+\[[^\]]+\]\s+(.+)$", line.strip())
    if not m:
        return None
    return m.group(1).strip()


def _existing_insight_fingerprints_from_learnings(text: str) -> set[str]:
    fps: set[str] = set()
    for line in text.splitlines():
        body = _insight_text_from_learnings_bullet(line)
        if body:
            fps.add(insight_fingerprint(body))
    return fps


def _promotable_insights(insights: Sequence[Insight], limit: int = 12) -> list[Insight]:
    sev_rank = {"error": 0, "warning": 1, "info": 2}
    scored: list[tuple[tuple[int, int, str], Insight]] = []
    for ins in insights:
        if ins.category == "lesson" or ins.severity in {"error", "warning"}:
            rank = (0 if ins.category == "lesson" else 1, sev_rank.get(ins.severity, 9), ins.text.lower())
            scored.append((rank, ins))
    scored.sort(key=lambda x: x[0])
    out: list[Insight] = []
    seen: set[str] = set()
    for _, ins in scored:
        fp = insight_fingerprint(ins.text)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(ins)
        if len(out) >= limit:
            break
    return out


def _merge_daily_note(path: Path, title_date: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    block = f"{MARKER_START}\n{body}\n{MARKER_END}\n"
    if not path.exists():
        path.write_text(f"# Memory — {title_date}\n\n{block}", encoding="utf-8")
        return
    existing = path.read_text(encoding="utf-8")
    if MARKER_START in existing and MARKER_END in existing:
        pre, rest = existing.split(MARKER_START, 1)
        _, post = rest.split(MARKER_END, 1)
        path.write_text(pre.rstrip() + "\n\n" + block + post.lstrip(), encoding="utf-8")
    else:
        path.write_text(existing.rstrip() + "\n\n" + block, encoding="utf-8")


def _append_learnings(root: Path, when: datetime, items: Sequence[Insight], dry_run: bool) -> Path | None:
    if not items:
        return None
    learn_dir = root / LEARNINGS_DIR
    path = learn_dir / LEARNINGS_FILE
    day = when.date().isoformat()
    existing_fps: set[str] = set()
    if path.exists():
        existing_fps = _existing_insight_fingerprints_from_learnings(path.read_text(encoding="utf-8"))

    additions: list[str] = []
    for ins in items:
        line = f"- [{day}] [{ins.severity.upper()}/{ins.category}] {ins.text}"
        fp = insight_fingerprint(ins.text)
        if fp in existing_fps:
            continue
        additions.append(line)
        existing_fps.add(fp)

    if not additions:
        return path if path.exists() else None

    section = "\n".join(
        [
            "",
            f"## Auto-promoted ({day} UTC)",
            "",
            "High-value lines derived from recent `memory/` reflections (deduplicated).",
            "",
            *additions,
            "",
        ]
    )
    if dry_run:
        return path

    learn_dir.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.write_text(path.read_text(encoding="utf-8").rstrip() + section + "\n", encoding="utf-8")
    else:
        path.write_text("# Learnings\n\n" + section.lstrip("\n") + "\n", encoding="utf-8")
    return path


def build_daily_markdown(
    run_at: datetime,
    *,
    lookback_days: int,
    files_scanned: list[str],
    errors: list[str],
    corrections: list[str],
    patterns: list[str],
    actions: list[str],
    insights: Sequence[Insight],
    error_correction_proximity_hits: int,
) -> str:
    lines = [
        f"## Self-reflection ({run_at.replace(microsecond=0).isoformat()})",
        "",
        f"- **Window:** last **{lookback_days}** days (UTC mtime on files under `{MEMORY_DIR}/`)",
        f"- **Files scanned:** {len(files_scanned)}",
        "",
        "### Errors found",
    ]
    if errors:
        for e in errors[:40]:
            lines.append(f"- {e}")
        if len(errors) > 40:
            lines.append(f"- _…and {len(errors) - 40} more_")
    else:
        lines.append("_No strong error/failure lines detected in the window._")
    lines.extend(["", "### Corrections made"])
    if corrections:
        for c in corrections[:40]:
            lines.append(f"- {c}")
        if len(corrections) > 40:
            lines.append(f"- _…and {len(corrections) - 40} more_")
    else:
        lines.append("_No explicit correction/fix language detected in the window._")

    lines.extend(
        [
            "",
            "### Patterns detected",
            f"- **Structured insights** (failure/lesson heuristics): **{len(insights)}** distinct",
            f"- **Error↔correction proximity** (within ±12 lines, same file): **{error_correction_proximity_hits}** hit(s)",
        ]
    )
    if patterns:
        for p in patterns:
            lines.append(f"- {p}")
    else:
        lines.append("- _No extra clustering beyond counts._")

    lines.append("")
    lines.append("### Suggested actions")
    for a in actions:
        lines.append(f"- {a}")

    lines.extend(["", "### Insight detail (deduplicated)"])
    if not insights:
        lines.append("_None — widen logging or ensure session notes land under `memory/`._")
    else:
        rank = {"error": 0, "warning": 1, "info": 2}
        for ins in sorted(insights, key=lambda i: (rank.get(i.severity, 9), i.text.lower()))[:30]:
            src = ", ".join(f"`{s}`" for s in ins.source_paths[:2])
            if len(ins.source_paths) > 2:
                src += ", …"
            lines.append(f"- **[{ins.severity.upper()}/{ins.category}]** {ins.text} _(sources: {src})_")
        if len(insights) > 30:
            lines.append(f"- _…{len(insights) - 30} additional insight(s) omitted for brevity_")

    lines.append("")
    lines.append("### Sources touched")
    for rel in files_scanned[:60]:
        lines.append(f"- `{rel}`")
    if len(files_scanned) > 60:
        lines.append(f"- _…and {len(files_scanned) - 60} more_")

    return "\n".join(lines).rstrip() + "\n"


@dataclass
class DailyReflectionResult:
    daily_path: Path
    files_scanned: list[str]
    errors_found: int
    corrections_found: int
    insights_count: int
    markdown: str


def run_daily_memory_reflection(
    root: Path,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    dry_run: bool = False,
) -> DailyReflectionResult:
    run_at = utc_now()
    cutoff = run_at - timedelta(days=lookback_days)
    daily_path = _daily_note_path(root, run_at)
    paths = _iter_memory_markdown(root, cutoff, exclude=daily_path)

    errors: list[str] = []
    corrections: list[str] = []
    for p in paths:
        e, c = _extract_error_correction_snippets(p, root)
        errors.extend(e)
        corrections.extend(c)

    insights = _insights_from_memory_files(paths, root)
    patterns = _pattern_clusters(insights)
    proximity_hits = _pair_count_for_files(paths)
    if proximity_hits:
        patterns.insert(0, f"{proximity_hits} file region(s) show error lines near correction language within a short window")

    actions = _suggested_actions(insights, errors, corrections)
    files_scanned = [p.relative_to(root).as_posix() for p in paths]

    md = build_daily_markdown(
        run_at,
        lookback_days=lookback_days,
        files_scanned=files_scanned,
        errors=errors,
        corrections=corrections,
        patterns=patterns,
        actions=actions,
        insights=insights,
        error_correction_proximity_hits=proximity_hits,
    )

    if not dry_run:
        _merge_daily_note(daily_path, run_at.date().isoformat(), md)
        promoted = _promotable_insights(insights)
        _append_learnings(root, run_at, promoted, dry_run=False)

    return DailyReflectionResult(
        daily_path=daily_path,
        files_scanned=files_scanned,
        errors_found=len(errors),
        corrections_found=len(corrections),
        insights_count=len(insights),
        markdown=md,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Daily `memory/` reflection: patterns, `memory/YYYY-MM-DD.md`, `.learnings/LEARNINGS.md`.",
    )
    p.add_argument("--root", type=Path, default=None, help="Workspace root (default: cwd or AUTO_REFLECTION_ROOT).")
    p.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"Days of file mtimes to include (default: {DEFAULT_LOOKBACK_DAYS}).",
    )
    p.add_argument("--dry-run", action="store_true", help="Do not write files; print summary to stdout.")
    p.add_argument("--stdout-summary", action="store_true", help="Print markdown body after run.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = _root_from_env(args.root)
    if args.dry_run:
        print(f"[dry-run] root={root}", file=sys.stderr)

    result = run_daily_memory_reflection(root, lookback_days=args.lookback_days, dry_run=args.dry_run)

    print(
        f"Scanned {len(result.files_scanned)} file(s); "
        f"errors={result.errors_found} corrections={result.corrections_found} "
        f"insights={result.insights_count}; daily_note={result.daily_path}",
        file=sys.stderr,
    )
    if args.stdout_summary or args.dry_run:
        print(result.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
