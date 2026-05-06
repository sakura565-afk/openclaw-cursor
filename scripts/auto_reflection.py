#!/usr/bin/env python3
"""Self-reflection cron script for OpenClaw session history.

The script scans recent transcript files from OpenClaw session storage, extracts
simple repeated patterns, writes a dated reflection into ``memory/``, and keeps
rolling quality metrics in ``.learnings/quality_metrics.json``.

Default config path:
    .learnings/auto_reflection_config.json

Example usage:
    python -m scripts.auto_reflection run --days 3
    python -m scripts.auto_reflection summary
    python -m scripts.auto_reflection digest
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


DEFAULT_CONFIG = {
    "lookback_days": 1,
    "max_transcripts": 25,
    "max_chars_per_transcript": 40000,
    "transcript_extensions": [".json", ".jsonl", ".log", ".md", ".txt"],
    "schedule": {
        "enabled": True,
        "minimum_interval_hours": 24,
    },
    "session_roots": [
        "{openclaw_home}/sessions",
        "{openclaw_home}/session_history",
        "{openclaw_home}/history/sessions",
        "{openclaw_home}/workspace/sessions",
        "{openclaw_home}/workspace/session_history",
        "{openclaw_home}/workspace/transcripts",
    ],
}

TEXT_KEYS = (
    "content",
    "text",
    "body",
    "message",
    "prompt",
    "response",
    "output",
    "input",
    "summary",
    "analysis",
    "result",
    "stderr",
    "stdout",
    "error",
)

POSITIVE_PATTERNS = (
    re.compile(r"\b(pass(?:ed|ing)?|verified|resolved|fixed|completed|implemented|working|successful|improved|optimized|added|shipped)\b", re.IGNORECASE),
    re.compile(r"\b(all tests pass|tests passed|build succeeded|looks good|went well)\b", re.IGNORECASE),
)
NEGATIVE_PATTERNS = (
    re.compile(r"\b(fail(?:ed|ing|ure)?|error|bug|broken|regression|timeout|blocked|missing|issue|warning|crash|conflict)\b", re.IGNORECASE),
    re.compile(r"\b(did not work|went wrong|not found|could not|unable to)\b", re.IGNORECASE),
)
ACTION_PATTERNS = (
    re.compile(r"\b(todo|follow[- ]up|next step|action item|should|need to|needs to|must|consider|document|add tests?|verify|clean up|refactor)\b", re.IGNORECASE),
    re.compile(r"\b(remember to|make sure|track|capture)\b", re.IGNORECASE),
)
VERIFICATION_PATTERNS = (
    re.compile(r"\b(test|verified|validation|assert|coverage|check)\b", re.IGNORECASE),
)
THEME_PATTERNS = {
    "testing": re.compile(r"\b(test|tests|coverage|assert|verify|validation|ci)\b", re.IGNORECASE),
    "context": re.compile(r"\b(context|memory|session|transcript|token)\b", re.IGNORECASE),
    "paths": re.compile(r"\b(path|file|directory|folder|locate|discover|storage)\b", re.IGNORECASE),
    "requirements": re.compile(r"\b(requirement|assumption|clarify|config|schedule|expected)\b", re.IGNORECASE),
    "tooling": re.compile(r"\b(cli|shell|command|subprocess|timeout|format|json)\b", re.IGNORECASE),
}
FILE_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+)"
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
LEADING_NOISE_RE = re.compile(
    r"^\s*(?:[-*+>]|\d+\.)\s*|^\s*\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2})?)?\s*[-:]\s*"
)
MULTISPACE_RE = re.compile(r"\s+")


@dataclass
class TranscriptRecord:
    path: Path
    modified_at: datetime
    size_bytes: int
    extracted_chars: int
    fragments: list[str]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_openclaw_home() -> Path:
    override = os.environ.get("OPENCLAW_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".openclaw"


def _json_load(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _json_dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def config_path(root: Path) -> Path:
    override = os.environ.get("OPENCLAW_REFLECTION_CONFIG")
    if override:
        return Path(override).expanduser()
    return root / ".learnings" / "auto_reflection_config.json"


def metrics_path(root: Path) -> Path:
    return root / ".learnings" / "quality_metrics.json"


def state_path(root: Path) -> Path:
    return root / ".learnings" / "auto_reflection_state.json"


def latest_report_from_state(root: Path) -> dict[str, Any] | None:
    payload = _json_load(state_path(root))
    if not payload:
        return None
    report = payload.get("last_report")
    return report if isinstance(report, dict) else None


def load_config(root: Path) -> tuple[dict[str, Any], Path]:
    path = config_path(root)
    payload = _json_load(path) or {}
    return _merge_dicts(DEFAULT_CONFIG, payload), path


def _resolve_session_roots(root: Path, config: dict[str, Any]) -> list[Path]:
    openclaw_home = _default_openclaw_home()
    raw_roots: list[str] = []

    env_value = os.environ.get("OPENCLAW_SESSION_ROOTS")
    if env_value:
        raw_roots.extend(part for part in env_value.split(os.pathsep) if part.strip())
    else:
        raw_roots.extend(str(item) for item in config.get("session_roots", []) if str(item).strip())

    resolved: list[Path] = []
    seen: set[str] = set()
    for raw_root in raw_roots:
        formatted = raw_root.format(
            openclaw_home=openclaw_home.as_posix(),
            workspace_root=root.as_posix(),
            home=str(Path.home()),
        )
        candidate = Path(formatted).expanduser()
        key = str(candidate.resolve()) if candidate.exists() else candidate.as_posix()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(candidate)
    return resolved


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _schedule_is_due(schedule: dict[str, Any], state: dict[str, Any] | None, now: datetime) -> tuple[bool, str | None]:
    if not bool(schedule.get("enabled", True)):
        return True, None

    minimum_interval_hours = float(schedule.get("minimum_interval_hours", 24))
    last_run_at = _parse_iso_datetime((state or {}).get("last_run_at"))
    if last_run_at is None:
        return True, None

    next_due = last_run_at + timedelta(hours=minimum_interval_hours)
    if now >= next_due:
        return True, None
    remaining = next_due - now
    remaining_hours = round(remaining.total_seconds() / 3600, 1)
    return False, f"Skipping reflection until schedule is due ({remaining_hours}h remaining)."


def _read_text(path: Path, limit: int | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if limit is not None and len(text) > limit:
        return text[:limit]
    return text


def _append_fragment(fragments: list[str], text: str, *, max_chars: int) -> None:
    cleaned = MULTISPACE_RE.sub(" ", text.replace("\x00", " ")).strip()
    if not cleaned:
        return
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars]
    fragments.append(cleaned)


def _flatten_json_text(value: Any, fragments: list[str], max_chars: int, depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(value, str):
        _append_fragment(fragments, value, max_chars=max_chars)
        return
    if isinstance(value, list):
        for item in value[:200]:
            _flatten_json_text(item, fragments, max_chars, depth + 1)
        return
    if not isinstance(value, dict):
        return

    role = value.get("role")
    if isinstance(role, str):
        role = role.strip().lower()
    else:
        role = None

    handled_keys: set[str] = set()
    for key in TEXT_KEYS:
        raw = value.get(key)
        if isinstance(raw, str):
            text = f"{role}: {raw}" if role and key in {"content", "message", "response", "output"} else raw
            _append_fragment(fragments, text, max_chars=max_chars)
            handled_keys.add(key)
        elif isinstance(raw, list):
            for item in raw[:100]:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    text = item["text"]
                    if role:
                        text = f"{role}: {text}"
                    _append_fragment(fragments, text, max_chars=max_chars)
            handled_keys.add(key)

    for key, raw in value.items():
        if key in handled_keys:
            continue
        _flatten_json_text(raw, fragments, max_chars, depth + 1)


def _extract_fragments(path: Path, max_chars: int) -> list[str]:
    suffix = path.suffix.lower()
    text = _read_text(path, limit=max_chars * 3)
    if not text:
        return []

    if suffix == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return [text[:max_chars]]
        fragments: list[str] = []
        _flatten_json_text(payload, fragments, max_chars)
        return fragments or [text[:max_chars]]

    if suffix == ".jsonl":
        fragments = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                _append_fragment(fragments, stripped, max_chars=max_chars)
                continue
            _flatten_json_text(payload, fragments, max_chars)
        return fragments or [text[:max_chars]]

    return [text[:max_chars]]


def discover_transcripts(root: Path, config: dict[str, Any], days: int, now: datetime) -> tuple[list[TranscriptRecord], list[Path]]:
    max_chars = int(config.get("max_chars_per_transcript", 40000))
    max_transcripts = int(config.get("max_transcripts", 25))
    allowed_extensions = {
        str(item).lower()
        for item in config.get("transcript_extensions", [".json", ".jsonl", ".log", ".md", ".txt"])
        if str(item).strip()
    }
    cutoff = now - timedelta(days=max(1, days))
    roots = _resolve_session_roots(root, config)

    candidates: list[Path] = []
    seen: set[str] = set()
    for session_root in roots:
        if not session_root.exists():
            continue
        if session_root.is_file():
            items = [session_root]
        else:
            items = [path for path in session_root.rglob("*") if path.is_file()]
        for path in items:
            if path.suffix.lower() not in allowed_extensions:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            if modified_at < cutoff:
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            candidates.append(path)

    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)

    records: list[TranscriptRecord] = []
    for path in candidates[:max_transcripts]:
        try:
            stat = path.stat()
        except OSError:
            continue
        fragments = _extract_fragments(path, max_chars=max_chars)
        extracted_chars = sum(len(item) for item in fragments)
        records.append(
            TranscriptRecord(
                path=path,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                size_bytes=stat.st_size,
                extracted_chars=extracted_chars,
                fragments=fragments,
            )
        )
    return records, roots


def _normalize_statement(text: str) -> str:
    stripped = LEADING_NOISE_RE.sub("", text.strip())
    stripped = stripped.replace("`", "")
    return MULTISPACE_RE.sub(" ", stripped).strip().lower()


def _clean_statement(text: str) -> str:
    cleaned = LEADING_NOISE_RE.sub("", text.strip())
    cleaned = MULTISPACE_RE.sub(" ", cleaned).strip(" -")
    return cleaned


def extract_candidate_statements(text: str) -> list[str]:
    chunks: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) <= 280:
            chunks.append(stripped)
        else:
            chunks.extend(part.strip() for part in SENTENCE_SPLIT_RE.split(stripped) if part.strip())

    statements: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        for part in SENTENCE_SPLIT_RE.split(chunk):
            cleaned = _clean_statement(part)
            normalized = _normalize_statement(cleaned)
            if len(cleaned) < 20 or len(cleaned) > 240:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            statements.append(cleaned)
    return statements


def _keyword_score(text: str, patterns: Iterable[re.Pattern[str]]) -> int:
    return sum(len(pattern.findall(text)) for pattern in patterns)


def classify_statement(text: str) -> tuple[str | None, int]:
    positive_score = _keyword_score(text, POSITIVE_PATTERNS)
    negative_score = _keyword_score(text, NEGATIVE_PATTERNS)
    action_score = _keyword_score(text, ACTION_PATTERNS)

    if action_score and action_score >= max(positive_score, negative_score):
        return "actionable_insights", action_score + 1
    if positive_score > negative_score and positive_score:
        return "what_went_well", positive_score
    if negative_score:
        return "what_went_wrong", negative_score
    return None, 0


def _detect_themes(text: str) -> set[str]:
    themes = set()
    for name, pattern in THEME_PATTERNS.items():
        if pattern.search(text):
            themes.add(name)
    return themes


def _top_items(entries: dict[str, dict[str, Any]], limit: int) -> list[str]:
    ranked = sorted(
        entries.values(),
        key=lambda item: (
            item["count"],
            item["score"],
            item["last_seen"],
            len(item["text"]),
        ),
        reverse=True,
    )
    return [item["text"] for item in ranked[:limit]]


def _focus_areas(transcripts: list[TranscriptRecord], limit: int = 5) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for record in transcripts:
        joined = "\n".join(record.fragments)
        for match in FILE_PATH_RE.finditer(joined):
            path = match.group("path")
            counts[path] = counts.get(path, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [{"path": path, "mentions": mentions} for path, mentions in ranked[:limit]]


def _derive_actionable_insights(
    direct_actions: list[str],
    theme_counts: dict[str, dict[str, int]],
    success_count: int,
    failure_count: int,
) -> list[str]:
    insights: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        normalized = _normalize_statement(text)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        insights.append(text)

    for item in direct_actions:
        add(item)

    if theme_counts["testing"]["negative"]:
        add("Run focused verification earlier in the edit loop so failures surface before the session gets long.")
    if theme_counts["context"]["negative"]:
        add("Summarize stale context sooner and trim transcript noise before it compounds across sessions.")
    if theme_counts["paths"]["negative"]:
        add("Lock down recurring storage paths in config so future runs spend less time rediscovering session locations.")
    if theme_counts["requirements"]["negative"]:
        add("Promote repeated assumptions into config or output headers so the next run starts with clearer intent.")
    if theme_counts["tooling"]["negative"]:
        add("Keep fallbacks for missing CLIs and unexpected transcript formats so the reflection pipeline still completes.")
    if success_count and not theme_counts["testing"]["negative"]:
        add("Preserve the habit of recording concrete verification outcomes; those signals make the reflections more reliable.")
    if failure_count > success_count:
        add("Break work into smaller checkpoints and close each one with a next step before moving on.")
    if not insights:
        add("End each session with one explicit success, one risk, and one next step to improve the next reflection run.")

    return insights[:5]


def _compute_quality_score(
    success_count: int,
    failure_count: int,
    action_count: int,
    verification_count: int,
    transcript_count: int,
    previous_score: int | None = None,
) -> int:
    if transcript_count == 0:
        return previous_score if previous_score is not None else 50

    score = 55
    score += min(success_count, 6) * 5
    score += min(action_count, 5) * 2
    score += min(verification_count, 4) * 3
    score -= min(failure_count, 6) * 6
    return max(0, min(100, score))


def analyze_transcripts(
    transcripts: list[TranscriptRecord],
    *,
    days: int,
    now: datetime,
    previous_score: int | None = None,
) -> dict[str, Any]:
    categorized = {
        "what_went_well": {},
        "what_went_wrong": {},
        "actionable_insights": {},
    }
    theme_counts = {
        name: {"positive": 0, "negative": 0, "action": 0}
        for name in THEME_PATTERNS
    }
    success_count = 0
    failure_count = 0
    action_count = 0
    verification_count = 0

    for record in transcripts:
        for fragment in record.fragments:
            for statement in extract_candidate_statements(fragment):
                category, score = classify_statement(statement)
                if category is None:
                    continue
                normalized = _normalize_statement(statement)
                bucket = categorized[category]
                existing = bucket.setdefault(
                    normalized,
                    {
                        "text": statement,
                        "count": 0,
                        "score": 0,
                        "last_seen": record.modified_at.isoformat(),
                    },
                )
                existing["count"] += 1
                existing["score"] += score
                if record.modified_at.isoformat() > existing["last_seen"]:
                    existing["last_seen"] = record.modified_at.isoformat()

                themes = _detect_themes(statement)
                if category == "what_went_well":
                    success_count += 1
                    for theme in themes:
                        theme_counts[theme]["positive"] += 1
                elif category == "what_went_wrong":
                    failure_count += 1
                    for theme in themes:
                        theme_counts[theme]["negative"] += 1
                else:
                    action_count += 1
                    for theme in themes:
                        theme_counts[theme]["action"] += 1

                if _keyword_score(statement, VERIFICATION_PATTERNS):
                    verification_count += 1

    well = _top_items(categorized["what_went_well"], 5)
    wrong = _top_items(categorized["what_went_wrong"], 5)
    direct_actions = _top_items(categorized["actionable_insights"], 5)
    insights = _derive_actionable_insights(direct_actions, theme_counts, success_count, failure_count)
    score = _compute_quality_score(
        success_count,
        failure_count,
        len(insights),
        verification_count,
        len(transcripts),
        previous_score=previous_score,
    )

    if not transcripts:
        wrong = [
            "No recent OpenClaw session transcripts were discovered under the configured session roots."
        ]
        insights = [
            "Set .learnings/auto_reflection_config.json to the right OpenClaw session storage paths before the next run."
        ]

    if not well:
        well = ["Reflection had limited positive signal density in the selected transcript window."]
    if not wrong:
        wrong = ["No dominant failure pattern repeated strongly enough to stand out in the selected window."]

    return {
        "reflection_date": now.date().isoformat(),
        "generated_at": now.isoformat(),
        "days": days,
        "summary": {
            "transcript_count": len(transcripts),
            "quality_score": score,
            "success_signal_count": success_count,
            "failure_signal_count": failure_count,
            "action_signal_count": len(insights),
            "verification_signal_count": verification_count,
        },
        "what_went_well": well,
        "what_went_wrong": wrong,
        "actionable_insights": insights,
        "focus_areas": _focus_areas(transcripts),
        "transcripts": [
            {
                "path": record.path.as_posix(),
                "modified_at": record.modified_at.isoformat(),
                "size_bytes": record.size_bytes,
                "extracted_chars": record.extracted_chars,
            }
            for record in transcripts
        ],
    }


def render_reflection_markdown(report: dict[str, Any], config: dict[str, Any], roots: list[Path]) -> str:
    summary = report["summary"]
    lines = [
        f"# OpenClaw Self Reflection - {report['reflection_date']}",
        "",
        f"- Generated at: {report['generated_at']}",
        f"- Lookback window: {report['days']} day(s)",
        f"- Quality score: {summary['quality_score']}/100",
        f"- Transcripts analyzed: {summary['transcript_count']}",
        f"- Schedule interval: {config['schedule'].get('minimum_interval_hours', 24)} hour(s)",
        "",
        "## What went well",
        "",
    ]
    for item in report["what_went_well"]:
        lines.append(f"- {item}")

    lines.extend(["", "## What went wrong", ""])
    for item in report["what_went_wrong"]:
        lines.append(f"- {item}")

    lines.extend(["", "## Actionable insights", ""])
    for item in report["actionable_insights"]:
        lines.append(f"- {item}")

    lines.extend(["", "## Focus areas", ""])
    if report["focus_areas"]:
        for item in report["focus_areas"]:
            lines.append(f"- `{item['path']}` mentioned {item['mentions']} time(s)")
    else:
        lines.append("- No repeated file focus areas were detected in the recent transcripts.")

    lines.extend(["", "## Session roots", ""])
    if roots:
        for root in roots:
            lines.append(f"- `{root.as_posix()}`")
    else:
        lines.append("- No session roots resolved from config.")

    lines.extend(["", "## Transcript coverage", ""])
    if report["transcripts"]:
        for item in report["transcripts"]:
            lines.append(
                f"- `{item['path']}` - modified {item['modified_at']}, {item['size_bytes']} bytes, {item['extracted_chars']} chars analyzed"
            )
    else:
        lines.append("- No transcripts were available in the selected time window.")

    return "\n".join(lines) + "\n"


def write_reflection(root: Path, report: dict[str, Any], config: dict[str, Any], roots: list[Path]) -> Path:
    memory_dir = root / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    output_path = memory_dir / f"{report['reflection_date']}-reflection.md"
    output_path.write_text(render_reflection_markdown(report, config, roots), encoding="utf-8")
    return output_path


def update_quality_metrics(root: Path, report: dict[str, Any], reflection_path: Path) -> dict[str, Any]:
    path = metrics_path(root)
    payload = _json_load(path) or {"history": []}
    history = payload.get("history")
    if not isinstance(history, list):
        history = []

    new_entry = {
        "date": report["reflection_date"],
        "score": report["summary"]["quality_score"],
        "transcript_count": report["summary"]["transcript_count"],
        "success_signal_count": report["summary"]["success_signal_count"],
        "failure_signal_count": report["summary"]["failure_signal_count"],
        "action_signal_count": report["summary"]["action_signal_count"],
        "reflection_path": reflection_path.as_posix(),
        "generated_at": report["generated_at"],
    }

    replaced = False
    for index, entry in enumerate(history):
        if isinstance(entry, dict) and entry.get("date") == new_entry["date"]:
            history[index] = new_entry
            replaced = True
            break
    if not replaced:
        history.append(new_entry)
    history = sorted(history, key=lambda item: item.get("date", ""))

    scores = [int(item["score"]) for item in history if isinstance(item, dict) and isinstance(item.get("score"), int)]
    current_score = new_entry["score"]
    previous_score = scores[-2] if len(scores) >= 2 else None
    if previous_score is None:
        trend = "new"
    elif current_score > previous_score:
        trend = "up"
    elif current_score < previous_score:
        trend = "down"
    else:
        trend = "flat"

    updated = {
        "current_score": current_score,
        "updated_at": report["generated_at"],
        "rolling_average": round(sum(scores) / len(scores), 1) if scores else float(current_score),
        "best_score": max(scores) if scores else current_score,
        "trend": trend,
        "history": history,
    }
    _json_dump(path, updated)
    return updated


def write_state(root: Path, report: dict[str, Any], reflection_path: Path, metrics: dict[str, Any]) -> None:
    payload = {
        "last_run_at": report["generated_at"],
        "last_reflection_path": reflection_path.as_posix(),
        "last_report": report,
        "current_quality_score": metrics.get("current_score"),
        "quality_trend": metrics.get("trend"),
    }
    _json_dump(state_path(root), payload)


def latest_reflection_path(root: Path) -> Path | None:
    memory_dir = root / "memory"
    if not memory_dir.exists():
        return None
    candidates = sorted(memory_dir.glob("*-reflection.md"))
    return candidates[-1] if candidates else None


def load_latest_context(root: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    return latest_report_from_state(root), _json_load(metrics_path(root))


def render_summary(report: dict[str, Any], metrics: dict[str, Any] | None) -> str:
    summary = report["summary"]
    lines = [
        "OpenClaw reflection summary",
        f"- Date: {report['reflection_date']}",
        f"- Quality score: {summary['quality_score']}/100",
        f"- Transcripts analyzed: {summary['transcript_count']}",
    ]
    if metrics:
        lines.append(f"- Rolling average: {metrics.get('rolling_average')}")
        lines.append(f"- Trend: {metrics.get('trend')}")

    lines.extend(["", "Key insights:"])
    for item in report["actionable_insights"][:3]:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def render_digest(report: dict[str, Any], metrics: dict[str, Any] | None) -> str:
    summary = report["summary"]
    score_text = f"{summary['quality_score']}/100"
    trend = metrics.get("trend") if metrics else "unknown"
    well = report["what_went_well"][0]
    wrong = report["what_went_wrong"][0]
    next_step = report["actionable_insights"][0]
    return (
        f"Reflection digest for {report['reflection_date']}. "
        f"Quality score {score_text} with trend {trend}. "
        f"Strongest positive pattern: {well} "
        f"Main risk: {wrong} "
        f"Best next move: {next_step}\n"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw self-reflection utility.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Analyze recent OpenClaw transcripts.")
    run_parser.add_argument("--days", type=int, default=None, help="Analyze the last N days of transcripts.")
    run_parser.add_argument("--force", action="store_true", help="Ignore schedule gating and run immediately.")

    subparsers.add_parser("summary", help="Show the latest reflection summary.")
    subparsers.add_parser("digest", help="Render the latest reflection as a concise reading digest.")
    return parser


def run_command(root: Path, args: argparse.Namespace, now: datetime) -> int:
    config, _ = load_config(root)
    state = _json_load(state_path(root)) or {}

    if not args.force:
        due, reason = _schedule_is_due(config.get("schedule", {}), state, now)
        if not due:
            print(reason)
            latest_path = latest_reflection_path(root)
            if latest_path is not None:
                print(f"Latest reflection: {latest_path.as_posix()}")
            return 0

    days = int(args.days or config.get("lookback_days", 1))
    metrics = _json_load(metrics_path(root)) or {}
    previous_score = metrics.get("current_score") if isinstance(metrics.get("current_score"), int) else None
    transcripts, roots = discover_transcripts(root, config, days, now)
    report = analyze_transcripts(transcripts, days=days, now=now, previous_score=previous_score)
    reflection_path = write_reflection(root, report, config, roots)
    updated_metrics = update_quality_metrics(root, report, reflection_path)
    write_state(root, report, reflection_path, updated_metrics)

    print(f"Wrote reflection to {reflection_path.as_posix()}")
    print(f"Quality score: {updated_metrics['current_score']}/100 ({updated_metrics['trend']})")
    print(f"Transcripts analyzed: {report['summary']['transcript_count']}")
    return 0


def summary_command(root: Path) -> int:
    report, metrics = load_latest_context(root)
    if report is None:
        latest_path = latest_reflection_path(root)
        if latest_path is None:
            print("No reflections have been generated yet.")
            return 1
        print(f"Latest reflection exists at {latest_path.as_posix()}, but no cached summary is available yet.")
        return 1
    print(render_summary(report, metrics), end="")
    return 0


def digest_command(root: Path) -> int:
    report, metrics = load_latest_context(root)
    if report is None:
        print("No reflections have been generated yet.")
        return 1
    print(render_digest(report, metrics), end="")
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path | None = None,
    now: datetime | None = None,
) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    workspace_root = Path(root or repo_root())
    current_time = now or _now_utc()

    if args.command == "run":
        return run_command(workspace_root, args, current_time)
    if args.command == "summary":
        return summary_command(workspace_root)
    if args.command == "digest":
        return digest_command(workspace_root)
    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
