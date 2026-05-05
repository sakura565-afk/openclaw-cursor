"""
Auto-reflection: scan session files, analyze patterns, write daily memory markdown.
Stdlib only (Python 3.10+).
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Regex patterns for error-like and success-like signals in session text
ERROR_PATTERNS = [
    re.compile(r"traceback", re.IGNORECASE),
    re.compile(r"exception", re.IGNORECASE),
    re.compile(r"\berror\b", re.IGNORECASE),
    re.compile(r"failed", re.IGNORECASE),
    re.compile(r"failure", re.IGNORECASE),
    re.compile(r"fatal", re.IGNORECASE),
    re.compile(r"errno\b", re.IGNORECASE),
    re.compile(r"crash", re.IGNORECASE),
    re.compile(r"undefined", re.IGNORECASE),
    re.compile(r"not found", re.IGNORECASE),
    re.compile(r"permission denied", re.IGNORECASE),
    re.compile(r"timeout", re.IGNORECASE),
]

SUCCESS_PATTERNS = [
    re.compile(r"\bpassed\b", re.IGNORECASE),
    re.compile(r"\bsuccess\b", re.IGNORECASE),
    re.compile(r"\bsucceeded\b", re.IGNORECASE),
    re.compile(r"completed successfully", re.IGNORECASE),
    re.compile(r"all (tests|checks) passed", re.IGNORECASE),
    re.compile(r"\bok\b", re.IGNORECASE),
    re.compile(r"\bdone\b", re.IGNORECASE),
    re.compile(r"no errors?", re.IGNORECASE),
]


def _sessions_dir(root_dir: str | Path | None) -> Path:
    env = os.environ.get("OPENCLAW_SESSIONS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    base = Path(root_dir) if root_dir else Path.cwd()
    return (base / "sessions").resolve()


def _extract_text_from_json(data: Any) -> str:
    """Pull human-readable text from common session JSON shapes."""
    parts: list[str] = []

    def add(s: Any) -> None:
        if s is None:
            return
        if isinstance(s, str) and s.strip():
            parts.append(s)
        elif isinstance(s, (list, tuple)):
            for x in s:
                add(x)
        elif isinstance(s, dict):
            for v in s.values():
                add(v)

    if isinstance(data, dict):
        for key in ("messages", "transcript", "content"):
            if key in data:
                add(data[key])
        if not parts:
            add(data)
    else:
        add(data)

    return "\n".join(parts) if parts else json.dumps(data, ensure_ascii=False, default=str)


def sessions_list(root_dir: str | Path | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """
    List recent session files as dicts: path, name, mtime_utc, text.
    Reads OPENCLAW_SESSIONS_DIR or <root>/sessions; extensions .json .md .txt; newest first.
    """
    sdir = _sessions_dir(root_dir)
    if not sdir.is_dir():
        return []

    candidates: list[Path] = []
    for ext in ("*.json", "*.md", "*.txt"):
        candidates.extend(sdir.glob(ext))

    # Sort by mtime descending
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    candidates = candidates[: max(0, limit)]

    out: list[dict[str, Any]] = []
    for path in candidates:
        try:
            st = path.stat()
        except OSError:
            continue
        mtime_utc = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        name = path.name
        text = ""
        try:
            if path.suffix.lower() == ".json":
                raw = path.read_text(encoding="utf-8", errors="replace")
                try:
                    data = json.loads(raw)
                    text = _extract_text_from_json(data)
                except json.JSONDecodeError:
                    text = raw
            else:
                text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""

        out.append(
            {
                "path": str(path),
                "name": name,
                "mtime_utc": mtime_utc,
                "text": text,
            }
        )
    return out


def _count_pattern_matches(text: str, patterns: list[re.Pattern[str]]) -> int:
    n = 0
    for pat in patterns:
        n += len(pat.findall(text))
    return n


def analyze_sessions(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Summarize sessions: error/success signal counts, per-file stats, combined text length.
    """
    error_hits = 0
    success_hits = 0
    per_file: list[dict[str, Any]] = []
    all_text_parts: list[str] = []

    for s in sessions:
        t = s.get("text") or ""
        all_text_parts.append(t)
        eh = _count_pattern_matches(t, ERROR_PATTERNS)
        sh = _count_pattern_matches(t, SUCCESS_PATTERNS)
        error_hits += eh
        success_hits += sh
        per_file.append(
            {
                "name": s.get("name", ""),
                "path": s.get("path", ""),
                "error_signals": eh,
                "success_signals": sh,
                "chars": len(t),
            }
        )

    combined = "\n".join(all_text_parts)
    return {
        "session_count": len(sessions),
        "error_signal_total": error_hits,
        "success_signal_total": success_hits,
        "combined_chars": len(combined),
        "per_file": per_file,
        "error_pattern_names": [p.pattern for p in ERROR_PATTERNS],
        "success_pattern_names": [p.pattern for p in SUCCESS_PATTERNS],
    }


def build_summary_markdown(
    analysis: dict[str, Any],
    day_iso: str,
    sessions: list[dict[str, Any]],
) -> str:
    """Build a markdown document for the memory file."""
    lines: list[str] = [
        f"# Auto-reflection — {day_iso}",
        "",
        "## Overview",
        "",
        f"- **Sessions scanned:** {analysis['session_count']}",
        f"- **Error-like signals (regex matches):** {analysis['error_signal_total']}",
        f"- **Success-like signals (regex matches):** {analysis['success_signal_total']}",
        f"- **Combined text size (chars):** {analysis['combined_chars']}",
        "",
        "## Session files (newest first)",
        "",
    ]
    for pf in analysis.get("per_file", []):
        lines.append(
            f"- `{pf.get('name', '')}` — errors: {pf.get('error_signals', 0)}, "
            f"success: {pf.get('success_signals', 0)}, chars: {pf.get('chars', 0)}"
        )
        lines.append(f"  - Path: `{pf.get('path', '')}`")

    lines.extend(
        [
            "",
            "## Patterns used",
            "",
            "**Error-oriented:** " + ", ".join(f"`{x}`" for x in analysis.get("error_pattern_names", [])),
            "",
            "**Success-oriented:** " + ", ".join(f"`{x}`" for x in analysis.get("success_pattern_names", [])),
            "",
            "## File list",
            "",
        ]
    )
    for s in sessions:
        lines.append(f"- {s.get('mtime_utc', '')} — `{s.get('name', '')}`")

    # Optional: top tokens from error-ish lines (simple word counter on lines with error signals)
    err_lines = []
    for s in sessions:
        for line in (s.get("text") or "").splitlines():
            if any(p.search(line) for p in ERROR_PATTERNS):
                err_lines.append(line.strip())
    if err_lines:
        words: list[str] = []
        for ln in err_lines:
            words.extend(re.findall(r"[A-Za-z0-9_]+", ln.lower()))
        common = Counter(words).most_common(15)
        lines.extend(["", "## Frequent tokens near error signals", ""])
        for w, c in common:
            lines.append(f"- `{w}`: {c}")

    lines.append("")
    return "\n".join(lines)


def run_auto_reflection(
    root_dir: str | Path,
    limit: int = 50,
    day: str | None = None,
    memory_subdir: str = "memory",
) -> Path:
    """
    Analyze sessions under OPENCLAW_SESSIONS_DIR or <root>/sessions and write
    <root>/<memory_subdir>/YYYY-MM-DD.md. Default day is today UTC.
    Returns path to written file.
    """
    root = Path(root_dir).resolve()
    if day:
        day_iso = day
    else:
        day_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    mem_dir = root / memory_subdir
    mem_dir.mkdir(parents=True, exist_ok=True)
    out_path = mem_dir / f"{day_iso}.md"

    sessions = sessions_list(root_dir=root, limit=limit)
    analysis = analyze_sessions(sessions)
    md = build_summary_markdown(analysis, day_iso, sessions)
    out_path.write_text(md, encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-reflection: summarize session files into memory markdown.")
    parser.add_argument("--root", type=str, default=".", help="Project root (sessions dir and memory output)")
    parser.add_argument("--limit", type=int, default=50, help="Max session files to read")
    parser.add_argument("--date", dest="date", type=str, default=None, help="YYYY-MM-DD for output filename (default: today UTC)")
    parser.add_argument("--memory-dir", dest="memory_dir", type=str, default="memory", help="Subdir under root for markdown")
    args = parser.parse_args()

    path = run_auto_reflection(
        root_dir=args.root,
        limit=args.limit,
        day=args.date,
        memory_subdir=args.memory_dir,
    )
    print(path)


if __name__ == "__main__":
    main()
