#!/usr/bin/env python3
"""Extract structured patterns from agent conversation logs and build a reviewable knowledge base."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Repo layout: scripts/ -> parent is repo root; skills live under src/skills/
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from skills import conversation_analyzer as ca  # noqa: E402


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def build_knowledge_base(
    logs_dir: Path,
    *,
    recursive: bool = False,
) -> dict[str, Any]:
    """Parse all logs, analyze patterns, and return a JSON-serializable knowledge base."""

    files = ca.discover_log_files(logs_dir, recursive=recursive)
    sessions: list[ca.ParsedSession] = []
    parse_errors: list[dict[str, str]] = []

    for fp in files:
        try:
            sessions.extend(ca.parse_log_file(fp))
        except OSError as exc:
            parse_errors.append({"path": fp.as_posix(), "error": str(exc)})

    analysis = ca.analyze_sessions(sessions)
    behaviors = ca.effective_behaviors(sessions)

    session_summaries: list[dict[str, Any]] = []
    for s in sessions:
        recovery = ca.recovery_events(s)
        session_summaries.append(
            {
                "source_path": s.source_path,
                "session_id": s.session_id,
                "outcome": s.outcome_label(),
                "turn_count": len(s.turns),
                "tool_sequence": s.tool_sequence,
                "decision_patterns_top": ca.decision_patterns(s)[:25],
                "recovery_event_count": len(recovery),
                "recovery_strategies": [r.get("strategy") for r in recovery if r.get("type") == "error_recovery"],
                "instruction_preview": ca.instruction_snippets(s, max_chars=200),
                "format": s.extras.get("format"),
            }
        )

    return {
        "generated_at": _utc_now_iso(),
        "logs_directory": logs_dir.resolve().as_posix(),
        "recursive_scan": recursive,
        "files_processed": len(files),
        "parse_errors": parse_errors,
        "sessions": session_summaries,
        "aggregate_analysis": analysis.to_dict(),
        "effective_behaviors": behaviors,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_markdown_summary(path: Path, kb: dict[str, Any]) -> None:
    """Human-readable report for manual review."""

    agg = kb.get("aggregate_analysis") or {}
    lines: list[str] = [
        "# Conversation extraction report",
        "",
        f"- Generated: `{kb.get('generated_at')}`",
        f"- Logs directory: `{kb.get('logs_directory')}`",
        f"- Files processed: **{kb.get('files_processed', 0)}**",
        f"- Sessions parsed: **{agg.get('session_count', 0)}**",
        "",
        "## Outcome distribution",
        "",
    ]

    for k, v in sorted((agg.get("outcome_counts") or {}).items(), key=lambda x: -x[1]):
        lines.append(f"- **{k}**: {v}")

    lines.extend(["", "## Tool usage chains (bigrams)", ""])

    bigrams = agg.get("tool_bigrams") or {}
    top_bi = sorted(bigrams.items(), key=lambda x: -x[1])[:25]
    if top_bi:
        for chain, count in top_bi:
            lines.append(f"- `{chain}` — {count}")
    else:
        lines.append("_No tool chains detected._")

    lines.extend(["", "## Decision / transition patterns", ""])
    dec = agg.get("decision_pattern_counts") or {}
    top_dec = sorted(dec.items(), key=lambda x: -x[1])[:30]
    if top_dec:
        for pattern, count in top_dec:
            lines.append(f"- `{pattern}` — {count}")
    else:
        lines.append("_No patterns detected._")

    lines.extend(["", "## Error recovery strategies", ""])
    rec = agg.get("recovery_strategy_counts") or {}
    if rec:
        for strat, count in sorted(rec.items(), key=lambda x: -x[1]):
            lines.append(f"- **{strat}**: {count}")
    else:
        lines.append("_No recovery events classified._")

    lines.extend(["", "## Recommendations", ""])
    for r in agg.get("recommendations") or []:
        lines.append(f"- {r}")
    if not agg.get("recommendations"):
        lines.append("_None._")

    lines.extend(["", "## Instruction signals (heuristic)", ""])
    lines.append("### More frequent in successful sessions")
    for row in (agg.get("instruction_success_correlation") or [])[:15]:
        lines.append(
            f"- `{row.get('token')}` — success={row.get('success_count')}, failure={row.get('failure_count')}"
        )
    lines.extend(["", "### More frequent in failed sessions"])
    for row in (agg.get("instruction_failure_correlation") or [])[:15]:
        lines.append(
            f"- `{row.get('token')}` — failure={row.get('failure_count')}, success={row.get('success_count')}"
        )

    lines.extend(["", "## Effective behaviors (mined tool chains)", ""])
    for b in kb.get("effective_behaviors") or []:
        lines.append(
            f"- `{b.get('pattern')}` — success_support={b.get('success_support')}, "
            f"failure_support={b.get('failure_support')}"
        )
    if not kb.get("effective_behaviors"):
        lines.append("_Insufficient recurring successful chains in corpus._")

    lines.extend(["", "## Session index", ""])
    for s in kb.get("sessions") or []:
        outcome = s.get("outcome")
        tc = s.get("turn_count")
        src = s.get("source_path")
        lines.append(f"- `{src}` — outcome={outcome}, turns={tc}")

    err = kb.get("parse_errors") or []
    if err:
        lines.extend(["", "## Parse errors", ""])
        for e in err:
            lines.append(f"- `{e.get('path')}`: {e.get('error')}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract conversation patterns from logs/ and write knowledge-base artifacts.",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("."),
        help="Repository root (default: current directory).",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=None,
        help="Directory containing conversation logs (default: <base-dir>/logs).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for JSON and Markdown (default: <base-dir>/logs/conversation_kb).",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Scan log directory recursively.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress summary line on stdout.",
    )
    args = parser.parse_args(argv)

    base_dir = args.base_dir.resolve()
    logs_dir = (args.logs_dir or base_dir / "logs").resolve()
    output_dir = (args.output_dir or logs_dir / "conversation_kb").resolve()

    _ensure_dir(output_dir)

    kb = build_knowledge_base(logs_dir, recursive=args.recursive)

    json_path = output_dir / "conversation_knowledge_base.json"
    md_path = output_dir / "conversation_summary_report.md"

    write_json(json_path, kb)
    write_markdown_summary(md_path, kb)

    if not args.quiet:
        print(f"Wrote {json_path.as_posix()}")
        print(f"Wrote {md_path.as_posix()}")
        print(
            f"Sessions: {kb['aggregate_analysis']['session_count']}, "
            f"files: {kb['files_processed']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
