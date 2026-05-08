#!/usr/bin/env python3
"""Periodic self-reflection over OpenClaw automation signals (logs, health), via Ollama.

Designed for cron: resolves paths from repo root, optional single-instance lock,
structured logging, and stable exit codes. Output is Markdown under ``logs/``.

Example crontab (daily 06:00 UTC, minimal PATH)::

    0 6 * * * cd /absolute/path/to/openclaw-cursor && \\
      /usr/bin/env PYTHONPATH=. /usr/bin/python3 -m scripts.auto_reflection run \\
      --root-dir /absolute/path/to/openclaw-cursor \\
      >> logs/auto_reflection_cron.log 2>&1

Environment:

- ``OLLAMA_MODEL``: model name (default: ``llama3.2``).
- ``OLLAMA_BIN``: path to ``ollama`` if not on ``PATH``.
- ``AUTO_REFLECTION_SKIP_LOCK=1``: allow overlapping runs (not recommended).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Generator, TextIO

# ---------------------------------------------------------------------------
# Repo root (for cron: cwd may be $HOME or /)
# ---------------------------------------------------------------------------


def default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Time helpers (UTC for logs + filenames; avoids DST surprises in cron)
# ---------------------------------------------------------------------------


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def format_iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class ExtractedInsights:
    """Structured fields extracted from model output; optional partial."""

    summary: str = ""
    themes: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    metrics_to_watch: list[str] = field(default_factory=list)
    experiments: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    raw_json: dict[str, Any] | None = None
    parse_notes: str = ""


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------


def _tail_lines(path: Path, max_lines: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text.strip()
    return "\n".join(lines[-max_lines:]).strip()


def iter_auto_improvement_snippets(
    log_dir: Path,
    *,
    since_days: int,
    max_entries: int,
    max_chars_per_entry: int,
) -> list[str]:
    """Load recent JSON lines from ``auto_improvements_*.json`` daily logs."""
    cutoff = (now_utc() - timedelta(days=since_days)).date()
    snippets: list[str] = []

    for path in sorted(log_dir.glob("auto_improvements_*.json")):
        stem = path.stem.rsplit("_", 1)[-1]
        try:
            day = datetime.strptime(stem, "%Y%m%d").date()
        except ValueError:
            continue
        if day < cutoff:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            snippets.append(f"- {path.name}: (invalid JSON, skipped)")
            continue
        if not isinstance(payload, list):
            continue
        for item in payload[-max_entries:]:
            if not isinstance(item, dict):
                continue
            cat = str(item.get("category", ""))
            action = str(item.get("action", ""))
            outcome = str(item.get("outcome", ""))
            ts = str(item.get("timestamp", ""))
            detail = json.dumps(item.get("details", {}), sort_keys=True)
            if len(detail) > max_chars_per_entry:
                detail = detail[: max_chars_per_entry - 3] + "..."
            snippets.append(f"- {ts} [{cat}] {action} → {outcome} {detail}")

    return snippets


def git_recent_activity(repo_root: Path, *, max_commits: int) -> str:
    git = shutil.which("git")
    if not git:
        return ""
    try:
        proc = subprocess.run(
            [git, "-C", str(repo_root), "log", f"-{max_commits}", "--oneline", "--no-decorate"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def build_context_bundle(
    repo_root: Path,
    *,
    log_dir: Path,
    since_days: int,
    max_log_entries: int,
    extra_paths: list[Path],
) -> str:
    parts: list[str] = []

    parts.append("## Recent auto-improvement / health log entries")
    snippets = iter_auto_improvement_snippets(
        log_dir,
        since_days=since_days,
        max_entries=max_log_entries,
        max_chars_per_entry=400,
    )
    if snippets:
        parts.append("\n".join(snippets[-200:]))  # cap overall size
    else:
        parts.append("(No auto_improvements_*.json entries in the window.)")

    digest_path = log_dir / "weekly_auto_improvement_digest.md"
    if digest_path.is_file():
        chunk = _tail_lines(digest_path, 120)
        if chunk:
            parts.append("\n## Tail: weekly_auto_improvement_digest.md\n```\n" + chunk + "\n```")

    activity = git_recent_activity(repo_root, max_commits=15)
    if activity:
        parts.append("\n## Recent git commits (oneline)\n")
        parts.append(activity)

    for extra in extra_paths:
        if extra.is_file():
            label = extra.relative_to(repo_root).as_posix() if extra.is_relative_to(repo_root) else str(extra)
            chunk = _tail_lines(extra, 80)
            if chunk:
                parts.append(f"\n## Tail: {label}\n```\n{chunk}\n```")

    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Prompts (two-stage mental model in one turn: analyze → decide → structure)
# ---------------------------------------------------------------------------


def reflection_system_prompt() -> str:
    return textwrap.dedent(
        """\
        You are a senior reliability engineer and meta-analyst for an autonomous
        Python automation stack (OpenClaw). You reflect on **signals only**—logs,
        health checks, and repo activity—not on private user data.

        Goals:
        - Find **patterns** (recurring failures, noisy warnings, missing signals).
        - Identify **risks** (resource exhaustion, silent failures, blind spots).
        - Propose **concrete, testable** improvements (scripts, thresholds, alerts).
        - Suggest **one small experiment** that would validate an assumption.

        Be candid and specific. If data is thin, say so and recommend what to log
        next time. Do not invent incidents not supported by the context."""
    ).strip()


def reflection_user_prompt(context: str) -> str:
    return textwrap.dedent(
        f"""\
        ### Context (may be partial)
        {context}

        ### Instructions
        1. Briefly summarize what the signals suggest about system behavior.
        2. List dominant themes (bullets).
        3. List risks or gaps (bullets).
        4. Give prioritized recommendations; each should start with a verb.
        5. Name metrics or checks worth watching.
        6. Optional: open questions for the next reflection.

        After your analysis, output **only** a JSON object in a fenced code block
        with language tag `json`, using **exactly** these keys (arrays may be empty strings omitted — use [] instead):
        ```json
        {{
          "summary": "one paragraph",
          "themes": ["..."],
          "risks": ["..."],
          "recommendations": ["..."],
          "metrics_to_watch": ["..."],
          "experiments": ["one small experiment"],
          "open_questions": ["..."]
        }}
        ```

        The JSON must be valid UTF-8 JSON (double quotes, no trailing commas)."""
    ).strip()


# ---------------------------------------------------------------------------
# Ollama invocation
# ---------------------------------------------------------------------------


def resolve_ollama_bin() -> str:
    env = os.environ.get("OLLAMA_BIN", "").strip()
    if env:
        return env
    found = shutil.which("ollama")
    if found:
        return found
    return "ollama"


def run_ollama_prompt(
    prompt: str,
    *,
    model: str,
    timeout: float,
    max_retries: int,
    backoff_base: float,
    ollama_bin: str,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleep_fn: Callable[[float], None] = time.sleep,
    time_fn: Callable[[], float] = time.monotonic,
) -> tuple[str, bool, str]:
    """Returns (stdout_text, success, failure_reason).

    On failure, stdout_text may still contain the last partial model output (if any),
    so callers can attempt insight extraction or include it in logs.
    """
    started = time_fn()
    last_err = ""
    last_stdout = ""
    for attempt in range(1, max_retries + 2):
        try:
            completed = run_command(
                [ollama_bin, "run", model, prompt],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            last_stdout = (completed.stdout or "").strip()
            if completed.returncode == 0:
                return last_stdout, True, ""
            stderr_txt = (completed.stderr or "").strip()
            last_err = stderr_txt or last_stdout or f"exit {completed.returncode}"
            if stderr_txt and last_stdout:
                last_stdout = f"{last_stdout}\n--- stderr ---\n{stderr_txt}"
            elif stderr_txt and not last_stdout:
                last_stdout = stderr_txt
        except subprocess.TimeoutExpired:
            last_err = f"timeout after {timeout}s"
        except OSError as exc:
            last_err = str(exc)

        if attempt <= max_retries:
            sleep_fn(backoff_base * (2 ** (attempt - 1)))

    return last_stdout, False, last_err or f"failed after {max_retries + 1} attempts"


# ---------------------------------------------------------------------------
# Insight extraction
# ---------------------------------------------------------------------------

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _normalize_str_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    if isinstance(val, list):
        out: list[str] = []
        for x in val:
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
        return out
    return []


def extract_insights_from_response(raw: str) -> ExtractedInsights:
    notes: list[str] = []
    parsed_obj: dict[str, Any] | None = None

    # Prefer the last valid JSON fence — prompts ask for analysis then a ```json``` block.
    fence_blocks = list(_JSON_FENCE.finditer(raw))
    for match in reversed(fence_blocks):
        block = match.group(1).strip()
        try:
            candidate = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            parsed_obj = candidate
            break
    if parsed_obj is None and fence_blocks:
        notes.append("fenced JSON blocks present but none parsed as an object")

    if parsed_obj is None:
        # Try whole response as JSON
        stripped = raw.strip()
        if stripped.startswith("{"):
            try:
                parsed_obj = json.loads(stripped)
            except json.JSONDecodeError:
                notes.append("response starts with '{' but is not valid JSON")

    if isinstance(parsed_obj, dict):
        summary = str(parsed_obj.get("summary", "") or "").strip()
        themes = _normalize_str_list(parsed_obj.get("themes"))
        risks = _normalize_str_list(parsed_obj.get("risks"))
        recs = _normalize_str_list(parsed_obj.get("recommendations"))
        metrics = _normalize_str_list(parsed_obj.get("metrics_to_watch"))
        experiments = _normalize_str_list(parsed_obj.get("experiments"))
        questions = _normalize_str_list(parsed_obj.get("open_questions"))
        return ExtractedInsights(
            summary=summary,
            themes=themes,
            risks=risks,
            recommendations=recs,
            metrics_to_watch=metrics,
            experiments=experiments,
            open_questions=questions,
            raw_json=parsed_obj,
            parse_notes="; ".join(notes) if notes else "",
        )

    # Fallback: lightweight markdown heuristics
    themes = _extract_bullet_section(raw, r"(?i)###?\s*themes?")
    risks = _extract_bullet_section(raw, r"(?i)###?\s*risks?")
    recs = _extract_bullet_section(raw, r"(?i)###?\s*recommendations?")
    metrics = _extract_bullet_section(raw, r"(?i)###?\s*metrics?")
    experiments = _extract_bullet_section(raw, r"(?i)###?\s*experiments?")
    questions = _extract_bullet_section(raw, r"(?i)###?\s*open questions?")

    summary = raw.strip()
    if len(summary) > 1200:
        summary = summary[:1197] + "..."

    notes.append("used heuristic bullet extraction (no JSON object found)")
    return ExtractedInsights(
        summary=summary,
        themes=themes,
        risks=risks,
        recommendations=recs,
        metrics_to_watch=metrics,
        experiments=experiments,
        open_questions=questions,
        parse_notes="; ".join(notes),
    )


def _extract_bullet_section(text: str, heading_pattern: str) -> list[str]:
    lines = text.splitlines()
    heading_re = re.compile(heading_pattern)
    start: int | None = None
    for i, line in enumerate(lines):
        if heading_re.search(line.strip()):
            start = i
            break
    if start is None:
        return []
    found: list[str] = []
    for line in lines[start + 1 :]:
        s = line.strip()
        if s.startswith("#"):
            break
        m = re.match(r"^[-*]\s+(.*)$", s)
        if m:
            found.append(m.group(1).strip())
    return found


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def render_markdown_report(
    *,
    repo_root: Path,
    generated_at: datetime,
    model: str,
    insights: ExtractedInsights,
    raw_model_output: str,
    context_stats: dict[str, Any],
    success: bool,
    error: str,
) -> str:
    ts = format_iso_z(generated_at)
    lines = [
        "# Auto-reflection digest",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Generated (UTC) | `{ts}` |",
        f"| Repository | `{repo_root}` |",
        f"| Model | `{model}` |",
        f"| LLM call | `{'ok' if success else 'failed'}` |",
    ]
    if error:
        lines.append(f"| Error | {error.replace('|', '\\|')} |")
    lines.extend(
        [
            "",
            "## Executive summary",
            "",
        ]
    )
    if insights.summary:
        lines.append(insights.summary)
    else:
        lines.append("_No summary extracted._")
    lines.append("")

    def section(title: str, items: list[str]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        if items:
            for item in items:
                lines.append(f"- {item}")
        else:
            lines.append("_None listed._")
        lines.append("")

    section("Themes", insights.themes)
    section("Risks and gaps", insights.risks)
    section("Recommendations", insights.recommendations)
    section("Metrics and checks to watch", insights.metrics_to_watch)
    section("Suggested experiments", insights.experiments)
    section("Open questions", insights.open_questions)

    lines.extend(
        [
            "## Context snapshot",
            "",
            "```json",
            json.dumps(context_stats, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )

    if insights.parse_notes:
        lines.append("> **Parse note:** " + insights.parse_notes.replace("\n", " "))
        lines.append("")
        lines.append("")

    lines.extend(
        [
            "## Model output (verbatim)",
            "",
            "<details>",
            "<summary>Raw LLM response</summary>",
            "",
            "```text",
            raw_model_output.strip() or "(empty)",
            "```",
            "",
            "</details>",
            "",
            "---",
            "",
            "*Generated by `scripts/auto_reflection.py`.*",
            "",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Lock file (stale detection via PID)
# ---------------------------------------------------------------------------


@contextmanager
def single_instance_lock(lock_path: Path, *, stale_seconds: int = 3 * 3600) -> Generator[None, None, None]:
    """
    Avoid overlapping cron runs. If lock exists and is older than ``stale_seconds``,
    or PID is dead, the lock is replaced.
    """
    skip = os.environ.get("AUTO_REFLECTION_SKIP_LOCK", "").strip() in {"1", "true", "yes"}
    if skip:
        yield
        return

    lock_path.parent.mkdir(parents=True, exist_ok=True)

    def read_lock() -> tuple[int | None, float | None]:
        try:
            raw = lock_path.read_text(encoding="utf-8").strip().split()
            pid_s = raw[0] if raw else ""
            pid = int(pid_s) if pid_s.isdigit() else None
            mtime = lock_path.stat().st_mtime
            return pid, mtime
        except OSError:
            return None, None

    def pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    if lock_path.exists():
        pid, mtime = read_lock()
        age = time.time() - (mtime or 0)
        stale = age > stale_seconds
        dead_pid = pid is not None and not pid_alive(pid)
        if not stale and not dead_pid:
            raise RuntimeError(f"another auto_reflection run holds {lock_path} (pid={pid})")

    lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    try:
        yield
    finally:
        try:
            if lock_path.exists():
                cur = lock_path.read_text(encoding="utf-8").strip().split()
                if cur and cur[0].isdigit() and int(cur[0]) == os.getpid():
                    lock_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Self-reflection cron over automation logs via Ollama.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Gather context, call the model, write Markdown.")
    run_p.add_argument("--root-dir", type=Path, default=default_repo_root(), help="Repository root.")
    run_p.add_argument("--log-dir", type=Path, default=None, help="Defaults to ROOT/logs.")
    run_p.add_argument("--since-days", type=int, default=14, help="Days of auto_improvements logs to include.")
    run_p.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", "llama3.2"), help="Ollama model name.")
    run_p.add_argument("--timeout", type=float, default=600.0, help="Per-invocation timeout (seconds).")
    run_p.add_argument("--retries", type=int, default=3, help="Retries after first failure.")
    run_p.add_argument("--backoff-base", type=float, default=1.5, help="Backoff base seconds.")
    run_p.add_argument("--dry-run", action="store_true", help="Skip Ollama; write placeholder output.")
    run_p.add_argument("--no-lock", action="store_true", help="Disable lock file (same as AUTO_REFLECTION_SKIP_LOCK).")
    run_p.add_argument("--extra-context", type=Path, nargs="*", default=[], help="Optional files to tail into context.")

    info_p = sub.add_parser("cron-suggestion", help="Print a sample crontab line for this repo.")
    info_p.add_argument("--root-dir", type=Path, default=default_repo_root())

    return p.parse_args(argv)


def output_paths(log_dir: Path, day: datetime) -> tuple[Path, Path]:
    day_str = day.strftime("%Y%m%d")
    md = log_dir / f"auto_reflection_{day_str}.md"
    json_sidecar = log_dir / f"auto_reflection_{day_str}.json"
    return md, json_sidecar


def command_run(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    root: Path = args.root_dir.resolve()
    log_dir = (args.log_dir or (root / "logs")).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    lock_path = log_dir / "auto_reflection.lock"
    if args.no_lock:
        os.environ["AUTO_REFLECTION_SKIP_LOCK"] = "1"

    extra = [Path(p).resolve() for p in args.extra_context]

    context = build_context_bundle(
        root,
        log_dir=log_dir,
        since_days=args.since_days,
        max_log_entries=50,
        extra_paths=extra,
    )

    stats = {
        "since_days": args.since_days,
        "context_chars": len(context),
        "log_dir": str(log_dir),
    }

    generated_at = now_utc()
    md_path, json_path = output_paths(log_dir, generated_at)

    try:
        with single_instance_lock(lock_path):
            if args.dry_run:
                insights = ExtractedInsights(
                    summary="Dry run: no model invoked.",
                    themes=["(dry-run)"],
                    recommendations=["Run without --dry-run after Ollama is available."],
                    parse_notes="dry_run",
                )
                body = render_markdown_report(
                    repo_root=root,
                    generated_at=generated_at,
                    model=args.model,
                    insights=insights,
                    raw_model_output="",
                    context_stats=stats,
                    success=True,
                    error="",
                )
                md_path.write_text(body, encoding="utf-8")
                json_path.write_text(
                    json.dumps({"insights": asdict(insights), "stats": stats}, indent=2, default=str),
                    encoding="utf-8",
                )
                print(str(md_path), file=stdout)
                return 0

            system_p = reflection_system_prompt()
            user_p = reflection_user_prompt(context)
            # Single message combining system + user for models that ignore multi-turn in `ollama run`.
            full_prompt = f"{system_p}\n\n---\n\n{user_p}"

            raw, ok, err = run_ollama_prompt(
                full_prompt,
                model=args.model,
                timeout=args.timeout,
                max_retries=args.retries,
                backoff_base=args.backoff_base,
                ollama_bin=resolve_ollama_bin(),
            )

            insights = extract_insights_from_response(raw)
            body = render_markdown_report(
                repo_root=root,
                generated_at=generated_at,
                model=args.model,
                insights=insights,
                raw_model_output=raw,
                context_stats=stats,
                success=ok,
                error=err if not ok else "",
            )
            md_path.write_text(body, encoding="utf-8")

            sidecar = {
                "generated_at": format_iso_z(generated_at),
                "model": args.model,
                "success": ok,
                "error": err if not ok else "",
                "insights": {
                    "summary": insights.summary,
                    "themes": insights.themes,
                    "risks": insights.risks,
                    "recommendations": insights.recommendations,
                    "metrics_to_watch": insights.metrics_to_watch,
                    "experiments": insights.experiments,
                    "open_questions": insights.open_questions,
                    "parse_notes": insights.parse_notes,
                },
                "stats": stats,
            }
            json_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True), encoding="utf-8")

            print(str(md_path), file=stdout)
            if not ok:
                print(f"auto_reflection: model error: {err}", file=stderr)
                return 2
            return 0
    except RuntimeError as exc:
        print(f"auto_reflection: {exc}", file=stderr)
        return 1


def command_cron_suggestion(args: argparse.Namespace, stdout: TextIO) -> int:
    root = args.root_dir.resolve()
    py = sys.executable
    line = (
        f"0 6 * * * cd {root} && PYTHONPATH=. {py} -m scripts.auto_reflection run "
        f"--root-dir {root} >> {root}/logs/auto_reflection_cron.log 2>&1"
    )
    print(line, file=stdout)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "cron-suggestion":
        return command_cron_suggestion(args, sys.stdout)
    return command_run(args, sys.stdout, sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
