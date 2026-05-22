#!/usr/bin/env python3
"""Automatic memory cleanup, promotion, session maintenance, and context splitting.

Run: ``python -m scripts.memory_hygiene_automator``

Steps:
  1. Remove exact semantic duplicates from ``MEMORY.md`` (same rules as ``memory_cleanup``).
  2. Promote blocks marked with ``<!-- openclaw-memory-promotion -->`` … ``<!-- /openclaw-memory-promotion -->``
     from ``memory/YYYY-MM-DD.md`` into ``MEMORY.md``.
  3. Run ``openclaw sessions cleanup --all-agents --enforce`` (use ``--dry-run`` when this script is
     invoked with ``--dry-run``). Retention age follows ``session.maintenance.pruneAfter`` in OpenClaw
     config; set it to ``7d`` for a seven-day cutoff.
  4. When ``openclaw status --json`` reports any recent session with ``totalTokens`` above the
     threshold, run structural ``context_split.split_context`` on the memory file (no LLM calls).
  5. Write ``scripts/data/memory_hygiene_report_YYYYMMDD.md``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

# Repo root on PYTHONPATH when running as ``python -m scripts.memory_hygiene_automator``
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import context_split  # noqa: E402
from scripts.memory_cleanup import (  # noqa: E402
    parse_file,
    rebuild_file,
)
from src.coordination.iskra_kara_shared_memory import resolve_openclaw_workspace  # noqa: E402

PROMOTION_BLOCK_RE = re.compile(
    r"<!--\s*openclaw-memory-promotion\s*-->(.*?)<!--\s*/openclaw-memory-promotion\s*-->",
    re.DOTALL | re.IGNORECASE,
)
DAILY_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
DEFAULT_SESSION_TOKEN_THRESHOLD = 50_000
SESSION_RETENTION_HINT_DAYS = 7


def _resolve_workspace() -> Path:
    override = os.environ.get("MEMORY_HYGIENE_WORKSPACE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return resolve_openclaw_workspace()


def _resolve_memory_md(workspace: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    env = os.environ.get("MEMORY_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    root_mem = REPO_ROOT / "MEMORY.md"
    if root_mem.exists():
        return root_mem.resolve()
    ws_mem = workspace / "MEMORY.md"
    return ws_mem.resolve()


def _resolve_daily_dir(workspace: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    env = os.environ.get("MEMORY_DAILY_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    repo_daily = REPO_ROOT / "memory"
    if repo_daily.is_dir():
        return repo_daily.resolve()
    return (workspace / "memory").resolve()


def _openclaw_base_cmd() -> list[str]:
    exe = os.environ.get("OPENCLAW_BIN", "").strip()
    if exe:
        return [exe]
    return ["npx", "--yes", "openclaw"]


def _run_openclaw(args: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    cmd = _openclaw_base_cmd() + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def dedupe_memory_md(memory_path: Path, *, dry_run: bool) -> dict[str, Any]:
    """Drop exact semantic duplicates (newest ``last_updated`` kept per ``semantic_text``)."""
    if not memory_path.exists():
        return {"skipped": True, "reason": "MEMORY.md missing", "removed": 0}

    parsed = parse_file(memory_path)
    active = list(parsed.entries)
    active.sort(key=lambda e: (e.last_updated, e.entry_id), reverse=True)

    duplicate_map: dict[str, Entry] = {}
    removed: list[dict[str, str]] = []
    deduped: list[Entry] = []
    for entry in active:
        if not entry.semantic_text:
            deduped.append(entry)
            continue
        existing = duplicate_map.get(entry.semantic_text)
        if existing is None:
            duplicate_map[entry.semantic_text] = entry
            deduped.append(entry)
            continue
        removed.append({"dropped": entry.entry_id, "kept": existing.entry_id})

    new_text = rebuild_file(parsed, deduped)
    changed = new_text != parsed.original_text
    if changed and not dry_run:
        memory_path.write_text(new_text, encoding="utf-8")

    return {
        "skipped": False,
        "removed": len(removed),
        "changed": changed,
        "dry_run": dry_run,
        "details": removed[:50],
    }


def extract_promotions(text: str) -> tuple[str, list[str]]:
    """Return (text_without_blocks, promoted_bodies)."""
    bodies: list[str] = []
    out_parts: list[str] = []
    pos = 0
    for match in PROMOTION_BLOCK_RE.finditer(text):
        out_parts.append(text[pos : match.start()])
        inner = match.group(1).strip()
        if inner:
            bodies.append(inner)
        pos = match.end()
    out_parts.append(text[pos:])
    cleaned = "".join(out_parts)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, bodies


def promote_daily_notes(
    memory_md: Path,
    daily_dir: Path,
    *,
    dry_run: bool,
    stamp: str,
) -> dict[str, Any]:
    if not daily_dir.is_dir():
        return {"skipped": True, "reason": "daily directory missing", "files": [], "blocks": 0}

    if not memory_md.exists():
        return {"skipped": True, "reason": "MEMORY.md missing for append", "files": [], "blocks": 0}

    promoted_from: list[dict[str, Any]] = []
    append_blocks: list[str] = []

    for path in sorted(daily_dir.glob("*.md")):
        if path.name == "MEMORY.md" or ".backup_" in path.name:
            continue
        if not DAILY_NAME_RE.match(path.name):
            continue
        raw = path.read_text(encoding="utf-8")
        cleaned, bodies = extract_promotions(raw)
        if not bodies:
            continue
        append_blocks.extend(bodies)
        promoted_from.append({"file": path.as_posix(), "blocks": len(bodies)})
        if cleaned != raw.strip() and not dry_run:
            note = f"\n\n> Auto: promoted {len(bodies)} block(s) to MEMORY.md on {stamp}.\n"
            path.write_text(cleaned.rstrip() + note, encoding="utf-8")

    if not append_blocks:
        return {"skipped": False, "files": [], "blocks": 0, "promoted_from": []}

    header = f"\n\n## Promoted memory ({stamp})\n\n"
    addition = header + "\n\n".join(block.strip() for block in append_blocks if block.strip()) + "\n"
    if not dry_run:
        existing = memory_md.read_text(encoding="utf-8").rstrip()
        memory_md.write_text(existing + addition, encoding="utf-8")

    return {
        "skipped": False,
        "blocks": len(append_blocks),
        "promoted_from": promoted_from,
        "dry_run": dry_run,
    }


def read_prune_after_hint() -> str | None:
    proc = _run_openclaw(["config", "get", "session.maintenance.pruneAfter"], timeout=30)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def run_openclaw_session_cleanup(*, dry_run: bool) -> dict[str, Any]:
    args = ["sessions", "cleanup", "--all-agents", "--json"]
    if dry_run:
        args.append("--dry-run")
    else:
        args.append("--enforce")
    proc = _run_openclaw(args, timeout=300)
    payload: Any = None
    if proc.stdout.strip():
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = {"raw": proc.stdout[:8000]}
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout[:12000],
        "stderr": proc.stderr[:4000],
        "json": payload,
    }


def openclaw_status_json() -> dict[str, Any] | None:
    proc = _run_openclaw(["status", "--json"], timeout=60)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def max_recent_session_tokens(status: dict[str, Any] | None) -> int:
    if not status:
        return 0
    recent = (status.get("sessions") or {}).get("recent") or []
    best = 0
    for row in recent:
        if not isinstance(row, dict):
            continue
        tt = row.get("totalTokens")
        if isinstance(tt, (int, float)):
            best = max(best, int(tt))
    return best


def run_structural_context_split(
    memory_path: Path,
    *,
    session_token_max: int,
    threshold: int,
    dry_run: bool,
) -> dict[str, Any]:
    """Split MEMORY.md text when sessions are hot; uses ``context_split`` without network calls."""
    if session_token_max < threshold:
        return {"skipped": True, "reason": "session tokens below threshold", "session_token_max": session_token_max}
    if not memory_path.exists():
        return {"skipped": True, "reason": "MEMORY.md missing"}

    text = context_split.normalize_text(memory_path.read_text(encoding="utf-8"))
    if not text:
        return {"skipped": True, "reason": "empty MEMORY.md"}

    tokens = context_split.estimate_tokens(text)
    chunks = context_split.split_context(
        text,
        chunk_size=threshold,
        overlap_tokens=min(5000, threshold // 10),
        split_threshold=threshold,
        recursive_limit=max(threshold * 2, 100_000),
        token_counter=context_split.estimate_tokens,
    )
    out_dir = REPO_ROOT / "scripts" / "data"
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")
    summary_path = out_dir / f"memory_context_split_summary_{stamp}.json"
    summary = {
        "memory_file": memory_path.as_posix(),
        "memory_tokens_estimated": tokens,
        "session_token_max": session_token_max,
        "n_chunks": len(chunks),
        "chunks": [
            {
                "index": c.index,
                "estimated_tokens": c.estimated_tokens,
                "overlap_tokens": c.overlap_tokens,
                "depth": c.depth,
            }
            for c in chunks
        ],
    }
    if not dry_run:
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return {
        "skipped": False,
        "memory_tokens_estimated": tokens,
        "n_chunks": len(chunks),
        "summary_path": summary_path.as_posix(),
        "dry_run": dry_run,
    }


@dataclass
class HygieneRun:
    stamp: str
    workspace: Path
    memory_md: Path
    daily_dir: Path
    dry_run: bool
    dedupe: dict[str, Any] = field(default_factory=dict)
    promotion: dict[str, Any] = field(default_factory=dict)
    prune_after_config: str | None = None
    session_cleanup: dict[str, Any] = field(default_factory=dict)
    context_split: dict[str, Any] = field(default_factory=dict)
    status_token_max: int = 0


def build_report(run: HygieneRun) -> str:
    lines = [
        f"# Memory hygiene report ({run.stamp})",
        "",
        f"- Workspace: `{run.workspace}`",
        f"- MEMORY.md: `{run.memory_md}`",
        f"- Daily notes dir: `{run.daily_dir}`",
        f"- Dry run: `{run.dry_run}`",
        "",
        "## Duplicate cleanup (MEMORY.md)",
        "",
        "```json",
        json.dumps(run.dedupe, indent=2),
        "```",
        "",
        "## Daily promotion (openclaw-memory-promotion)",
        "",
        "```json",
        json.dumps(run.promotion, indent=2),
        "```",
        "",
        "## OpenClaw session maintenance",
        "",
        f"- Recommended retention for 'older than {SESSION_RETENTION_HINT_DAYS} days': set "
        f"`session.maintenance.pruneAfter` to `\"7d\"` in OpenClaw config, then re-run this script.",
        "",
        f"- Configured `session.maintenance.pruneAfter` (if readable): `{run.prune_after_config}`",
        "",
        "```json",
        json.dumps(run.session_cleanup, indent=2)[:16000],
        "```",
        "",
        "## Context split (structural)",
        "",
        f"- Recent session token max (from `openclaw status --json`): **{run.status_token_max}**",
        "",
        "```json",
        json.dumps(run.context_split, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


def run_hygiene(
    *,
    dry_run: bool,
    memory_path: Path | None,
    daily_dir: Path | None,
    token_threshold: int,
) -> HygieneRun:
    workspace = _resolve_workspace()
    memory_md = _resolve_memory_md(workspace, memory_path)
    daily = _resolve_daily_dir(workspace, daily_dir)
    stamp = date.today().strftime("%Y%m%d")
    run = HygieneRun(
        stamp=stamp,
        workspace=workspace,
        memory_md=memory_md,
        daily_dir=daily,
        dry_run=dry_run,
    )

    run.dedupe = dedupe_memory_md(memory_md, dry_run=dry_run)
    run.promotion = promote_daily_notes(memory_md, daily, dry_run=dry_run, stamp=stamp)
    run.prune_after_config = read_prune_after_hint()
    run.session_cleanup = run_openclaw_session_cleanup(dry_run=dry_run)

    status = openclaw_status_json()
    run.status_token_max = max_recent_session_tokens(status)
    run.context_split = run_structural_context_split(
        memory_md,
        session_token_max=run.status_token_max,
        threshold=token_threshold,
        dry_run=dry_run,
    )

    report_dir = REPO_ROOT / "scripts" / "data"
    if not dry_run:
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"memory_hygiene_report_{stamp}.md"
        report_path.write_text(build_report(run), encoding="utf-8")
        print(f"Report written: {report_path}")
    else:
        print(build_report(run))

    return run


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Memory hygiene automator for MEMORY.md and OpenClaw.")
    parser.add_argument("--dry-run", action="store_true", help="Do not write files; print report to stdout.")
    parser.add_argument("--memory", type=Path, default=None, help="Override path to MEMORY.md.")
    parser.add_argument("--daily-dir", type=Path, default=None, help="Override directory for YYYY-MM-DD.md files.")
    parser.add_argument(
        "--session-token-threshold",
        type=int,
        default=int(os.environ.get("MEMORY_HYGIENE_SESSION_TOKEN_THRESHOLD", DEFAULT_SESSION_TOKEN_THRESHOLD)),
        help=f"Run structural context split when any recent session meets/exceeds this many tokens "
        f"(default: {DEFAULT_SESSION_TOKEN_THRESHOLD}).",
    )
    args = parser.parse_args(argv)

    try:
        run_hygiene(
            dry_run=args.dry_run,
            memory_path=args.memory,
            daily_dir=args.daily_dir,
            token_threshold=args.session_token_threshold,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"OpenClaw command timed out: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Failed to run OpenClaw: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
