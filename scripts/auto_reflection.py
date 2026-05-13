#!/usr/bin/env python3
"""
Self-reflection over OpenClaw session history (~/.openclaw/sessions/).

Scans recent session transcripts, extracts errors, corrections, and decisions,
and writes a dated markdown report under ~/.openclaw/.learnings/ using the same
structured run pattern as other self-improvement tooling (YAML front matter,
actionable follow-ups).

Example:

    python3 scripts/auto_reflection.py --days 3
    python3 -m scripts.auto_reflection --days 1 --stdout-summary

Environment:

- OPENCLAW_HOME — override ~/.openclaw (default: ``Path.home() / ".openclaw"``)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

MAX_FILE_BYTES = 2 * 1024 * 1024
DEFAULT_DAYS = 7
LEARNINGS_SUBDIR = ".learnings"

# --- Pattern sets (aligned with scripts/conversation_extractor decision/learning cues) ---

FAILURE_HINTS = re.compile(
    r"(?i)(\b(traceback|exception|error|failed|failure|timed?\s*out|exit\s*code\s*[1-9]\d*)\b|"
    r"\b(Fatal|Critical)\b|^Error:|\[\s*ERROR\s*\])"
)

CORRECTION_HINTS = re.compile(
    r"(?i)(\bcorrection\b|\bcorrected\b|\bto clarify\b|\bclarification\b|"
    r"\bI meant\b|\bactually,?\b|\binstead of\b|\bI was wrong\b|\bmy mistake\b|"
    r"\bon second thought\b|\bretract(?:ing)?\b|\bfixed\s*:\b|\brevised\b|"
    r"\bupdated (?:the |our )?approach\b|\bshould have\b|\broot cause\b|"
    r"\blesson learned\b|\btakeaway\b|\bnext time\b)"
)

DECISION_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:decision|resolution|resolved|outcome)\s*[:\-—]\s*(.+)",
        r"^\s*\*{0,2}(?:decision|resolution)\*{0,2}\s*[:\-—]\s*(.+)",
        r"(?:we(?:'ve)?\s+(?:decided|agreed|chose)|let'?s\s+go\s+with|final(?:ly)?\s*:\s*)(.+)",
        r"(?:\bapproved\b|\bfinalize[ds]?\b|\bchosen\b\s+(?:approach|option|path))\s*[:\-]?\s*(.+)",
        r"^\s*(?:TL;DR|TDLR|takeaway)s?\s*[:\-—]\s*(.+)",
        r"\b(?:concluded|conclusion)\s+(?:that\s+)?(.{10,})",
        r"\bdecision\s*[:\-—]\s*(.+)",
    )
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def openclaw_home_path() -> Path:
    raw = os.environ.get("OPENCLAW_HOME", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".openclaw").resolve()


def sessions_dir(home: Path) -> Path:
    return (home / "sessions").resolve()


def learnings_dir(home: Path) -> Path:
    return (home / LEARNINGS_SUBDIR).resolve()


def rel_under_sessions(sessions_root: Path, path: Path) -> str:
    try:
        return path.relative_to(sessions_root).as_posix()
    except ValueError:
        return path.name


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _ensure_repo_on_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def normalize_snippet(text: str, limit: int = 480) -> str:
    line = re.sub(r"\s+", " ", text.strip())
    return line[:limit]


def event_fingerprint(kind: str, text: str) -> str:
    return hashlib.sha256(f"{kind}|{text.lower()}".encode("utf-8")).hexdigest()[:16]


@dataclass
class ReflectionEvent:
    """One surfaced transcript moment."""

    kind: str  # error | correction | decision
    text: str
    source: str
    severity: str = "info"  # info | warning | error

    def fingerprint(self) -> str:
        return event_fingerprint(self.kind, self.text)


@dataclass
class ReflectionRun:
    """Serializable outcome of a reflection pass (self-improving-agent style envelope)."""

    run_id: str
    generated_at_utc: str
    days: int
    openclaw_home: str
    sessions_scanned: int
    files: list[str]
    events: list[ReflectionEvent]
    summary_markdown: str
    output_path: str = ""


def _severity_for_error_line(line: str) -> str:
    if re.search(r"(?i)\b(traceback|exception|fatal|critical)\b", line):
        return "error"
    if FAILURE_HINTS.search(line):
        return "warning"
    return "info"


def classify_line(line: str) -> ReflectionEvent | None:
    stripped = line.strip()
    if len(stripped) < 16:
        return None

    if FAILURE_HINTS.search(stripped):
        return ReflectionEvent(
            kind="error",
            text=normalize_snippet(stripped),
            source="",
            severity=_severity_for_error_line(stripped),
        )

    if CORRECTION_HINTS.search(stripped):
        return ReflectionEvent(
            kind="correction",
            text=normalize_snippet(stripped),
            source="",
            severity="info",
        )

    for pat in DECISION_LINE_PATTERNS:
        m = pat.search(stripped)
        if m:
            body = m.group(1).strip() if m.lastindex else stripped
            if len(body) < 12:
                body = stripped
            return ReflectionEvent(
                kind="decision",
                text=normalize_snippet(body),
                source="",
                severity="info",
            )

    return None


def dedupe_events(events: Iterable[ReflectionEvent]) -> list[ReflectionEvent]:
    buckets: dict[str, ReflectionEvent] = {}
    for ev in events:
        fp = ev.fingerprint()
        cur = buckets.get(fp)
        if cur is None:
            buckets[fp] = ReflectionEvent(
                kind=ev.kind,
                text=ev.text,
                source=ev.source,
                severity=ev.severity,
            )
        else:
            sev_rank = {"error": 3, "warning": 2, "info": 1}
            if sev_rank.get(ev.severity, 0) > sev_rank.get(cur.severity, 0):
                cur.severity = ev.severity
    return list(buckets.values())


def _walk_json_strings(node: Any, chunks: list[str], *, min_len: int) -> None:
    if isinstance(node, str):
        s = node.strip()
        if len(s) >= min_len:
            chunks.append(s)
        return
    if isinstance(node, dict):
        for v in node.values():
            _walk_json_strings(v, chunks, min_len=min_len)
        return
    if isinstance(node, list):
        for v in node:
            _walk_json_strings(v, chunks, min_len=min_len)


def _segments_from_json_file(path: Path) -> list[str]:
    _ensure_repo_on_path()
    try:
        from scripts.conversation_extractor import parse_json_session

        segs = [text for _, _, text in parse_json_session(path) if text.strip()]
        if segs:
            return segs
    except Exception:
        pass

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [raw]

    chunks: list[str] = []
    _walk_json_strings(data, chunks, min_len=24)
    return chunks


def _segments_from_jsonl(path: Path) -> list[str]:
    out: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("messages"), list):
            try:
                out.extend(_segments_from_json_str(json.dumps({"messages": obj["messages"]})))
            except (TypeError, ValueError):
                walk_obj(obj, out)
        else:
            walk_obj(obj, out)
    return out


def _segments_from_json_str(raw: str) -> list[str]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [raw]
    chunks: list[str] = []
    _walk_json_strings(data, chunks, min_len=24)
    return chunks


def walk_obj(obj: Any, out: list[str]) -> None:
    _walk_json_strings(obj, out, min_len=24)


def iter_transcript_chunks(path: Path) -> list[str]:
    suf = path.suffix.lower()
    if suf == ".json":
        return _segments_from_json_file(path)
    if suf == ".jsonl":
        return _segments_from_jsonl(path)
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if not raw.strip():
        return []
    if suf in {".md", ".txt", ".log"} or not suf:
        return raw.splitlines()
    # Unknown extension: try JSON first, else lines
    try:
        json.loads(raw)
    except json.JSONDecodeError:
        return raw.splitlines()
    return _segments_from_json_str(raw)


def extract_events_for_file(path: Path, sessions_root: Path) -> Iterator[ReflectionEvent]:
    try:
        rel = path.relative_to(sessions_root).as_posix()
    except ValueError:
        rel = path.name

    for chunk in iter_transcript_chunks(path):
        if "\n" in chunk:
            lines = chunk.splitlines()
        else:
            lines = [chunk]

        for line in lines:
            hit = classify_line(line)
            if hit is None:
                continue
            hit.source = rel
            yield hit


def iter_session_files(sessions_root: Path, cutoff: datetime) -> list[Path]:
    if not sessions_root.is_dir():
        return []

    out: list[Path] = []
    for path in sessions_root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            continue
        if st.st_size > MAX_FILE_BYTES:
            continue
        out.append(path)

    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def _suggest_followups(events: Sequence[ReflectionEvent]) -> list[str]:
    """Short improvement hooks derived from event mix (self-improving-agent pattern)."""

    if not events:
        return [
            "No high-signal events in this window; keep capturing transcripts for richer reflection.",
        ]

    suggestions: list[str] = []
    err_n = sum(1 for e in events if e.kind == "error")
    corr_n = sum(1 for e in events if e.kind == "correction")
    dec_n = sum(1 for e in events if e.kind == "decision")

    if err_n:
        suggestions.append(
            f"Schedule a focused pass on the top {min(err_n, 3)} error themes above; "
            "triage whether each is tooling, policy, or model drift."
        )
    if corr_n > dec_n * 2:
        suggestions.append(
            "Corrections dominate decisions; consider tightening upfront constraints or "
            "checklists before long tool chains."
        )
    if dec_n and not err_n:
        suggestions.append(
            "Decisions without surfaced errors: capture rationale in session templates "
            "so future runs can diff intent vs outcome."
        )
    if not suggestions:
        suggestions.append(
            "Review decision and correction sections together and promote any recurring "
            "theme into a skill or workspace rule."
        )
    return suggestions[:5]


def build_summary_markdown(
    run_at: datetime,
    days: int,
    home: Path,
    session_files: Sequence[Path],
    events: Sequence[ReflectionEvent],
    followups: Sequence[str],
) -> str:
    lines = [
        f"# OpenClaw auto-reflection — {run_at.date().isoformat()} (UTC)",
        "",
        f"- **Window:** last **{days}** day(s)",
        f"- **OpenClaw home:** `{home}`",
        f"- **Session files scanned:** {len(session_files)}",
        f"- **Distinct key events:** {len(events)}",
        "",
    ]

    if session_files:
        lines.append("## Session files (recent first)")
        for p in session_files[:25]:
            try:
                rel = p.relative_to(home / "sessions").as_posix()
            except ValueError:
                rel = p.as_posix()
            lines.append(f"- `{rel}`")
        if len(session_files) > 25:
            lines.append(f"- _…and {len(session_files) - 25} more_")
        lines.append("")

    if not events:
        lines.append("_No errors, corrections, or explicit decisions matched the heuristics in this window._")
    else:
        by_kind: dict[str, list[ReflectionEvent]] = {"error": [], "correction": [], "decision": []}
        for ev in events:
            by_kind.setdefault(ev.kind, []).append(ev)

        rank = {"error": 0, "warning": 1, "info": 2}
        for kind, title in (
            ("error", "Errors"),
            ("correction", "Corrections"),
            ("decision", "Decisions"),
        ):
            bucket = by_kind.get(kind, [])
            if not bucket:
                continue
            lines.append(f"## {title}")
            for ev in sorted(bucket, key=lambda e: (rank.get(e.severity, 9), e.text.lower())):
                badge = ev.severity.upper() if kind == "error" else "NOTE"
                lines.append(f"- **[{badge}]** {ev.text} _( `{ev.source}` )_")
            lines.append("")

        ctr = Counter(e.kind for e in events)
        lines.append("## Event counts")
        for k, n in ctr.most_common():
            lines.append(f"- **{k}**: {n}")
        lines.append("")

    lines.append("## Self-improvement — next pass")
    for item in followups:
        lines.append(f"- {item}")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_front_matter(
    run: ReflectionRun,
    followups: Sequence[str],
) -> str:
    payload = {
        "id": "openclaw-auto-reflection",
        "name": "OpenClaw session auto-reflection",
        "generated_at_utc": run.generated_at_utc,
        "openclaw_home": run.openclaw_home,
        "scan_days": run.days,
        "sessions_scanned": run.sessions_scanned,
        "distinct_events": len(run.events),
        "self_improvement": {"next_actions": list(followups)},
    }
    # YAML-ish without external dependency: json is valid structured metadata readers can parse
    return "---\n" + json.dumps(payload, indent=2, ensure_ascii=False) + "\n---\n\n"


def run_reflection(
    home: Path,
    *,
    days: int,
    run_at: datetime | None = None,
    dry_run: bool = False,
) -> ReflectionRun:
    now = run_at or utc_now()
    started = now.replace(microsecond=0)
    cutoff = started - timedelta(days=days)

    sdir = sessions_dir(home)
    session_files = iter_session_files(sdir, cutoff)

    events: list[ReflectionEvent] = []
    for sf in session_files:
        events.extend(extract_events_for_file(sf, sdir))

    events = dedupe_events(events)
    events.sort(key=lambda e: (e.kind, e.severity, e.text.lower()))

    followups = _suggest_followups(events)
    summary_body = build_summary_markdown(started, days, home, session_files, events, followups)

    run_id = started.strftime("%Y%m%d_%H%M%S")
    out_path = learnings_dir(home) / f"auto_reflection_{started.date().isoformat()}.md"
    full_markdown = build_front_matter(
        ReflectionRun(
            run_id=run_id,
            generated_at_utc=started.isoformat(),
            days=days,
            openclaw_home=str(home),
            sessions_scanned=len(session_files),
            files=[rel_under_sessions(sdir, p) for p in session_files],
            events=events,
            summary_markdown="",
        ),
        followups,
    ) + summary_body

    run = ReflectionRun(
        run_id=run_id,
        generated_at_utc=started.isoformat(),
        days=days,
        openclaw_home=str(home),
        sessions_scanned=len(session_files),
        files=[rel_under_sessions(sdir, p) for p in session_files],
        events=events,
        summary_markdown=full_markdown,
        output_path=str(out_path),
    )

    if dry_run:
        return run

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(full_markdown, encoding="utf-8")

    try:
        from src.coordination.iskra_kara_shared_memory import notify_kara_from_iskra

        notify_kara_from_iskra(
            "reflection",
            {
                "summary_markdown": summary_body,
                "run_id": run.run_id,
                "files_scanned": run.sessions_scanned,
                "event_count": len(run.events),
                "output_path": str(out_path),
            },
        )
    except Exception:
        pass

    return run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Scan ~/.openclaw/sessions for errors, corrections, and decisions; write .learnings summary.",
    )
    p.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        metavar="N",
        help=f"Include session files modified in the last N days (default: {DEFAULT_DAYS}).",
    )
    p.add_argument(
        "--openclaw-home",
        type=Path,
        default=None,
        help="Override OpenClaw home (default: OPENCLAW_HOME or ~/.openclaw).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write the markdown file; still builds the summary in memory.",
    )
    p.add_argument(
        "--stdout-summary",
        action="store_true",
        help="Print the full markdown document to stdout.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    home = (args.openclaw_home.expanduser().resolve() if args.openclaw_home else openclaw_home_path())

    if args.dry_run:
        print(f"[dry-run] openclaw_home={home}", file=sys.stderr)

    if args.days < 1:
        print("--days must be >= 1", file=sys.stderr)
        return 2

    run = run_reflection(home, days=args.days, dry_run=args.dry_run)

    if args.dry_run:
        print(f"[dry-run] would write: {run.output_path}", file=sys.stderr)
    else:
        print(f"Wrote {run.output_path}", file=sys.stderr)

    if args.stdout_summary:
        print(run.summary_markdown, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
