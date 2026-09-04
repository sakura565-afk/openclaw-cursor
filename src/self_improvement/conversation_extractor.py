#!/usr/bin/env python3
"""
Parse session transcripts and store structured learnings under ``.learnings/conversations/``.

Extracts per session:
- decisions made
- errors encountered
- tool usage patterns
- key insights (lessons, takeaways, reusable patterns)

Daily output: ``.learnings/conversations/YYYY-MM-DD.json``

Run from repo root::

    python -m src.self_improvement.conversation_extractor
    python -m src.self_improvement.conversation_extractor --session path/to/session.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from scripts.conversation_extractor import analyze_segments, parse_session_log

LEARNINGS_DIR = ".learnings"
CONVERSATIONS_SUBDIR = "conversations"
SCHEMA_VERSION = 1
ARTIFACT_TYPE = "conversation_daily_learnings"

DEFAULT_SINCE_HOURS = 24
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SCANNED_FILES = 200

DEFAULT_SESSION_GLOBS: tuple[str, ...] = (
    "logs/**/*.log",
    "logs/**/*.json",
    "memory/**/*_log.md",
    "memory/**/*.md",
    "memory/**/conversation_extract_*.json",
    "**/session.json",
)

FAILURE_HINTS = re.compile(
    r"(?i)(\b(traceback|exception|error|failed|failure|timed?\s*out|exit\s*code\s*[1-9]\d*)\b|"
    r"\b(Fatal|Critical)\b|^Error:|\[\s*ERROR\s*\])"
)

ERROR_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:error|failure|exception|traceback)\s*[:\-—]\s*(.+)",
        r"^\s*\*{0,2}(?:error|failure)\*{0,2}\s*[:\-—]\s*(.+)",
        r"(?:^|\s)(?:Error|ERROR|Exception):\s*(.+)",
        r"\b(exit\s*code\s*[1-9]\d*)\b",
        r"\b(timed?\s*out)\b",
    )
)

INSIGHT_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:insight|key\s+insight|takeaway|note)\s*[:\-—]\s*(.+)",
        r"^\s*\*{0,2}(?:important|remember)\*{0,2}\s*[:\-—]\s*(.+)",
    )
)


def repo_root() -> Path:
    env = os.environ.get("CONVERSATION_EXTRACTOR_ROOT") or os.environ.get("AUTO_REFLECTION_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dedupe(items: list[str], *, max_items: int = 200) -> list[str]:
    return list(dict.fromkeys(x for x in items if x.strip()))[:max_items]


def _normalize_line(line: str, *, limit: int = 400) -> str:
    compact = re.sub(r"\s+", " ", line.strip())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _match_line_patterns(text: str, patterns: tuple[re.Pattern[str], ...]) -> list[str]:
    hits: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for pat in patterns:
            m = pat.search(stripped)
            if m:
                cap = (m.group(1) if m.lastindex else m.group(0)).strip()
                hits.append(_normalize_line(cap))
                break
    return hits


def extract_errors_from_segments(segments: list[tuple[int, str | None, str]]) -> list[str]:
    errors: list[str] = []
    for _turn, _role, text in segments:
        for line in text.splitlines():
            stripped = line.strip()
            if len(stripped) < 8:
                continue
            if FAILURE_HINTS.search(stripped):
                errors.append(_normalize_line(stripped))
                continue
            errors.extend(_match_line_patterns(stripped, ERROR_LINE_PATTERNS))
    return _dedupe(errors)


def extract_insights_from_segments(segments: list[tuple[int, str | None, str]]) -> list[str]:
    extra: list[str] = []
    for _turn, role, text in segments:
        rl = (role or "").lower()
        if rl in {"tool"}:
            continue
        extra.extend(_match_line_patterns(text, INSIGHT_LINE_PATTERNS))
    return _dedupe(extra)


def build_tool_usage_patterns(digest_tools: Counter[str]) -> dict[str, Any]:
    ranked = digest_tools.most_common()
    top = [name for name, _ in ranked[:10]]
    return {
        "tools_ranked": [{"name": name, "count": count} for name, count in ranked],
        "top_tools": top,
        "distinct_tool_count": len(ranked),
        "total_mentions": sum(digest_tools.values()),
    }


@dataclass
class SessionExtraction:
    """Structured extraction from one transcript."""

    source: str
    extracted_at_utc: str
    decisions_made: list[str] = field(default_factory=list)
    errors_encountered: list[str] = field(default_factory=list)
    tool_usage_patterns: dict[str, Any] = field(default_factory=dict)
    key_insights: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "extracted_at_utc": self.extracted_at_utc,
            "decisions_made": self.decisions_made,
            "errors_encountered": self.errors_encountered,
            "tool_usage_patterns": self.tool_usage_patterns,
            "key_insights": self.key_insights,
        }


def extract_session(path: Path, *, root: Path) -> SessionExtraction | None:
    segments = parse_session_log(path.resolve())
    if not segments:
        return None

    rel = rel_under_root(path.resolve(), root)
    digest = analyze_segments(segments, rel)
    segment_insights = extract_insights_from_segments(segments)

    key_insights = _dedupe(
        list(digest.learnings) + list(digest.patterns) + segment_insights,
    )

    return SessionExtraction(
        source=rel,
        extracted_at_utc=utc_now().isoformat(),
        decisions_made=list(digest.decisions),
        errors_encountered=extract_errors_from_segments(segments),
        tool_usage_patterns=build_tool_usage_patterns(digest.all_tools()),
        key_insights=key_insights,
    )


def rel_under_root(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def session_dirs_from_env() -> tuple[Path, ...]:
    raw = os.environ.get("AUTO_REFLECTION_SESSION_DIRS", "")
    roots: list[Path] = []
    for part in raw.split(","):
        p = Path(part.strip()).expanduser()
        if p.is_dir():
            roots.append(p.resolve())
    return tuple(dict.fromkeys(roots))


def iter_session_files(
    root: Path,
    globs: Sequence[str],
    cutoff: datetime,
    *,
    extra_roots: Sequence[Path] | None = None,
) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    bases = [root.resolve()]
    if extra_roots:
        bases.extend(Path(p).resolve() for p in extra_roots if p.is_dir())

    for base in bases:
        for pattern in globs:
            for path in base.glob(pattern):
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
    if len(out) > MAX_SCANNED_FILES:
        return out[:MAX_SCANNED_FILES]
    return out


def session_fingerprint(path: Path) -> str:
    try:
        st = path.stat()
        payload = f"{path.resolve()}:{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        payload = str(path.resolve())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def conversations_path(root: Path, day: date) -> Path:
    return root / LEARNINGS_DIR / CONVERSATIONS_SUBDIR / f"{day.isoformat()}.json"


def _empty_daily_payload(day: date) -> dict[str, Any]:
    return {
        "artifact_type": ARTIFACT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "date": day.isoformat(),
        "generated_at_utc": utc_now().isoformat(),
        "sessions": [],
        "summary": {
            "session_count": 0,
            "decisions_made": [],
            "errors_encountered": [],
            "tool_usage_patterns": build_tool_usage_patterns(Counter()),
            "key_insights": [],
        },
        "processed_fingerprints": [],
    }


def load_daily_payload(path: Path, day: date) -> dict[str, Any]:
    if not path.is_file():
        return _empty_daily_payload(day)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_daily_payload(day)
    if not isinstance(data, dict):
        return _empty_daily_payload(day)
    data.setdefault("sessions", [])
    data.setdefault("processed_fingerprints", [])
    data.setdefault("summary", _empty_daily_payload(day)["summary"])
    return data


def aggregate_sessions(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    decisions: list[str] = []
    errors: list[str] = []
    insights: list[str] = []
    tool_counter: Counter[str] = Counter()

    for row in sessions:
        decisions.extend(row.get("decisions_made") or [])
        errors.extend(row.get("errors_encountered") or [])
        insights.extend(row.get("key_insights") or [])
        patterns = row.get("tool_usage_patterns") or {}
        for item in patterns.get("tools_ranked") or []:
            if isinstance(item, dict):
                name = item.get("name")
                count = item.get("count", 1)
                if isinstance(name, str) and name.strip():
                    try:
                        tool_counter[name] += int(count)
                    except (TypeError, ValueError):
                        tool_counter[name] += 1

    return {
        "session_count": len(sessions),
        "decisions_made": _dedupe(decisions),
        "errors_encountered": _dedupe(errors),
        "tool_usage_patterns": build_tool_usage_patterns(tool_counter),
        "key_insights": _dedupe(insights),
    }


def merge_session_into_daily(
    payload: dict[str, Any],
    extraction: SessionExtraction,
    fingerprint: str,
) -> bool:
    """Append session if fingerprint not seen. Returns True when merged."""

    seen = set(payload.get("processed_fingerprints") or [])
    if fingerprint in seen:
        return False

    sessions = payload.setdefault("sessions", [])
    sessions.append(extraction.as_dict())
    seen.add(fingerprint)
    payload["processed_fingerprints"] = sorted(seen)
    payload["summary"] = aggregate_sessions(sessions)
    payload["generated_at_utc"] = utc_now().isoformat()
    return True


def write_daily_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_daily_extraction(
    root: Path,
    *,
    day: date | None = None,
    since_hours: int = DEFAULT_SINCE_HOURS,
    globs: Sequence[str] | None = None,
    session_paths: Sequence[Path] | None = None,
    extra_roots: Sequence[Path] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Scan transcripts, merge into the daily JSON file, return the payload."""

    root = root.resolve()
    target_day = day or utc_now().date()
    out_path = conversations_path(root, target_day)
    payload = load_daily_payload(out_path, target_day)

    if session_paths:
        files = [p.resolve() for p in session_paths if p.is_file()]
    else:
        cutoff = utc_now() - timedelta(hours=since_hours)
        patterns = tuple(globs or DEFAULT_SESSION_GLOBS)
        files = iter_session_files(root, patterns, cutoff, extra_roots=extra_roots)

    merged = 0
    for path in files:
        extraction = extract_session(path, root=root)
        if extraction is None:
            continue
        fp = session_fingerprint(path)
        if merge_session_into_daily(payload, extraction, fp):
            merged += 1

    if not dry_run:
        write_daily_payload(out_path, payload)

    payload["_output_path"] = out_path.as_posix()
    payload["_sessions_merged_this_run"] = merged
    return payload


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract decisions, errors, tool patterns, and insights from session transcripts "
        "into .learnings/conversations/YYYY-MM-DD.json",
    )
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Workspace root (default: repo root or CONVERSATION_EXTRACTOR_ROOT).",
    )
    p.add_argument(
        "--date",
        type=str,
        default=None,
        help="Target calendar day YYYY-MM-DD (default: today UTC).",
    )
    p.add_argument(
        "--since-hours",
        type=int,
        default=DEFAULT_SINCE_HOURS,
        help=f"Only scan files modified within this many hours (default: {DEFAULT_SINCE_HOURS}).",
    )
    p.add_argument(
        "--session",
        type=Path,
        action="append",
        dest="sessions",
        default=None,
        help="Explicit session transcript path (repeatable). Skips glob scan when set.",
    )
    p.add_argument(
        "--glob",
        action="append",
        dest="globs",
        default=None,
        help="Extra glob pattern relative to --root (repeatable).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute merge result but do not write .learnings/conversations/*.json.",
    )
    p.add_argument(
        "--stdout",
        action="store_true",
        help="Print resulting JSON payload to stdout.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    root = (args.root or repo_root()).resolve()

    target_day: date | None = None
    if args.date:
        try:
            target_day = date.fromisoformat(args.date)
        except ValueError:
            sys.stderr.write(f"error: invalid --date {args.date!r} (use YYYY-MM-DD)\n")
            return 2

    globs = tuple(DEFAULT_SESSION_GLOBS)
    if args.globs:
        globs = globs + tuple(args.globs)

    payload = run_daily_extraction(
        root,
        day=target_day,
        since_hours=args.since_hours,
        globs=globs,
        session_paths=tuple(args.sessions) if args.sessions else None,
        extra_roots=session_dirs_from_env(),
        dry_run=args.dry_run,
    )

    out_path = payload.pop("_output_path", "")
    merged = payload.pop("_sessions_merged_this_run", 0)

    if args.stdout:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        action = "would write" if args.dry_run else "wrote"
        print(f"{action} {out_path} ({merged} new session(s) merged)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
