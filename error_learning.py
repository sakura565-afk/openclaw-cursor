#!/usr/bin/env python3
"""Self-improvement error capture: persist incidents, detect patterns, suggest agent prompts."""

from __future__ import annotations

import json
import logging
import re
import threading
import traceback
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

__all__ = ("ErrorLearningSystem",)

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

# Normalized pattern identifiers (stable API for analyze_patterns / suggest_fixes).
PATTERN_FILE_NOT_FOUND = "file_not_found"
PATTERN_PERMISSION_DENIED = "permission_denied"
PATTERN_TIMEOUT = "timeout"
PATTERN_RATE_LIMIT = "rate_limit"

_PATTERN_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        PATTERN_FILE_NOT_FOUND,
        (
            "filenotfounderror",
            "errno 2",
            "no such file or directory",
            "no such file",
            "cannot find the file",
            "cannot find the path",
            "failed to open stream",
            "system cannot find the file",
        ),
    ),
    (
        PATTERN_PERMISSION_DENIED,
        (
            "permissionerror",
            "permission denied",
            "errno 13",
            "eacces",
            "operation not permitted",
            "access is denied",
        ),
    ),
    (
        PATTERN_TIMEOUT,
        (
            "timeout",
            "timed out",
            "time out",
            "deadline exceeded",
            "etimedout",
            "asyncio.timeouterror",
            "read timed out",
            "connection timed out",
        ),
    ),
    (
        PATTERN_RATE_LIMIT,
        (
            "rate limit",
            "rate limited",
            "too many requests",
            "429",
            "throttl",
            "quota exceeded",
            "resource exhausted",
        ),
    ),
)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _default_errors_dir() -> Path:
    return Path(__file__).resolve().parent / ".learnings" / "errors"


def _normalize_scan_text(*parts: str) -> str:
    return " ".join(p.lower() for p in parts if p).strip()


def detect_error_patterns(message: str, stack_trace: str = "") -> list[str]:
    """Return sorted unique pattern ids implied by message and stack trace."""

    blob = _normalize_scan_text(message, stack_trace)
    if not blob:
        return []
    found: set[str] = set()
    for pattern_id, needles in _PATTERN_RULES:
        if any(needle in blob for needle in needles):
            found.add(pattern_id)
    return sorted(found)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(dict(payload), indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read error record %s: %s", path, exc)
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Skipping corrupt JSON at %s: %s", path, exc)
        return None
    if not isinstance(parsed, dict):
        logger.warning("Skipping non-object JSON at %s", path)
        return None
    return parsed


def _coerce_context(ctx: Mapping[str, Any] | None) -> dict[str, Any]:
    if ctx is None:
        return {}
    if not isinstance(ctx, Mapping):
        raise TypeError("context must be a mapping or None.")
    out: dict[str, Any] = {}
    for key, value in ctx.items():
        if not isinstance(key, str):
            raise TypeError("context keys must be strings.")
        try:
            json.dumps(value)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"context value for {key!r} is not JSON-serializable.") from exc
        out[key] = value
    return out


@dataclass(frozen=True)
class _RemediationSpec:
    headline: str
    agent_prompt: str


_REMEDIATIONS: dict[str, _RemediationSpec] = {
    PATTERN_FILE_NOT_FOUND: _RemediationSpec(
        headline="Resolve missing paths and assets",
        agent_prompt=(
            "You are working in a repository where operations failed with a file-not-found class error.\n"
            "Goals:\n"
            "1. Locate every path referenced in the failing stack trace and verify it exists from the "
            "process working directory.\n"
            "2. Fix incorrect relative paths, stale configuration, or missing generated artifacts; add "
            "mkdir/create steps only when the design requires new files.\n"
            "3. Add or adjust tests or CI steps so the required files are created or checked in before use.\n"
            "4. Summarize which path was wrong, what you changed, and how a regression is prevented.\n"
            "Work incrementally and keep edits minimal and consistent with existing project conventions."
        ),
    ),
    PATTERN_PERMISSION_DENIED: _RemediationSpec(
        headline="Fix permissions and secure access",
        agent_prompt=(
            "You are a Cursor agent addressing permission-denied failures.\n"
            "Goals:\n"
            "1. Identify whether the failure is filesystem permissions, OS sandboxing, or missing credentials.\n"
            "2. Prefer least-privilege fixes: correct ownership/mode on intended directories, run commands "
            "as the right user, or write to user-writable locations instead of broad chmod.\n"
            "3. If secrets are involved, load them from the project's documented env or secret store—never "
            "hard-code tokens.\n"
            "4. Document the root cause and the durable fix for future contributors."
        ),
    ),
    PATTERN_TIMEOUT: _RemediationSpec(
        headline="Harden timeouts and slow dependencies",
        agent_prompt=(
            "You are a Cursor agent mitigating timeout-related failures.\n"
            "Goals:\n"
            "1. Map which network, disk, or compute step exceeded its deadline and whether the limit is "
            "configurable.\n"
            "2. Add retries with backoff and jitter only where idempotent; otherwise surface partial progress "
            "or reduce workload size.\n"
            "3. Improve observability: log durations, active endpoints, and payload sizes to spot regressions.\n"
            "4. Propose code or config changes that keep user-visible latency acceptable while preserving safety."
        ),
    ),
    PATTERN_RATE_LIMIT: _RemediationSpec(
        headline="Handle rate limits and quotas",
        agent_prompt=(
            "You are a Cursor agent resolving rate-limit or quota exhaustion.\n"
            "Goals:\n"
            "1. Inspect HTTP status, headers (Retry-After), and SDK messages to classify vendor throttling vs. "
            "application bugs.\n"
            "2. Implement respectful backoff, request coalescing, caching, or batching aligned with vendor guidance.\n"
            "3. Surface actionable operator guidance when keys, plans, or quotas must change.\n"
            "4. Add tests or monitors proving the client stays under limits during nominal workloads."
        ),
    ),
}


class ErrorLearningSystem:
    """Capture structured errors under `.learnings/errors/`, analyze patterns, emit agent prompts."""

    def __init__(self, errors_dir: Path | None = None) -> None:
        self._errors_dir = Path(errors_dir) if errors_dir is not None else _default_errors_dir()
        self._lock = threading.RLock()

    @property
    def errors_dir(self) -> Path:
        return self._errors_dir

    def capture_error(
        self,
        exc: BaseException | None = None,
        *,
        error_type: str | None = None,
        message: str = "",
        stack_trace: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> str:
        """
        Persist one error record as JSON. Pass either ``exc`` or both ``error_type`` and ``message``.

        Returns the generated record id (also used as the filename stem).
        """

        if exc is not None and not isinstance(exc, BaseException):
            raise TypeError("exc must be a BaseException instance or None.")

        if exc is not None:
            err_type = type(exc).__name__
            msg = str(exc) or err_type
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        else:
            if not error_type or not str(error_type).strip():
                raise ValueError("error_type is required when exc is not provided.")
            err_type = str(error_type).strip()
            msg = message if message else err_type
            tb = stack_trace if stack_trace is not None else ""

        ctx = _coerce_context(context)
        patterns = detect_error_patterns(msg, tb)
        record_id = uuid.uuid4().hex
        ts = _utc_timestamp()
        payload: dict[str, Any] = {
            "schema_version": _SCHEMA_VERSION,
            "id": record_id,
            "timestamp": ts,
            "error_type": err_type,
            "message": msg,
            "context": ctx,
            "stack_trace": tb,
            "patterns": patterns,
        }

        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", err_type)[:80] or "Error"
        path = self._errors_dir / f"{ts.replace(':', '-')}_{safe_name}_{record_id[:8]}.json"

        with self._lock:
            try:
                _atomic_write_json(path, payload)
            except OSError as exc_write:
                logger.error("Failed to write error record to %s: %s", path, exc_write)
                raise

        logger.info("Captured error %s (%s) -> %s", record_id, err_type, path)
        return record_id

    def _iter_records(self) -> list[dict[str, Any]]:
        if not self._errors_dir.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(self._errors_dir.glob("*.json")):
            data = _safe_read_json(path)
            if not data:
                continue
            data["_source_path"] = str(path)
            records.append(data)
        return records

    def get_recent_errors(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return up to ``limit`` most recent records (newest first), without internal keys."""

        if limit < 1:
            raise ValueError("limit must be at least 1.")
        rows = self._iter_records()

        def sort_key(item: dict[str, Any]) -> str:
            return str(item.get("timestamp") or "")

        rows.sort(key=sort_key, reverse=True)
        out: list[dict[str, Any]] = []
        for raw in rows[:limit]:
            clean = {k: v for k, v in raw.items() if not k.startswith("_")}
            out.append(clean)
        return out

    def analyze_patterns(
        self,
        *,
        limit: int | None = None,
        min_occurrences: int = 2,
    ) -> list[dict[str, Any]]:
        """
        Scan stored errors for known patterns and return summaries meeting ``min_occurrences``.

        Each item includes pattern id, counts, representative error types, and recent timestamps.
        """

        if min_occurrences < 1:
            raise ValueError("min_occurrences must be at least 1.")
        rows = self._iter_records()
        rows.sort(key=lambda r: str(r.get("timestamp") or ""), reverse=True)
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be at least 1 when provided.")
            rows = rows[:limit]

        pattern_to_types: dict[str, Counter[str]] = {}
        pattern_counts: Counter[str] = Counter()
        pattern_last_ts: dict[str, str] = {}

        for row in rows:
            ts = str(row.get("timestamp") or "")
            et = str(row.get("error_type") or "Unknown")
            plist = row.get("patterns")
            if isinstance(plist, list):
                patterns = [str(p) for p in plist if isinstance(p, str)]
            else:
                msg = str(row.get("message") or "")
                st = str(row.get("stack_trace") or "")
                patterns = detect_error_patterns(msg, st)

            for p in patterns:
                pattern_counts[p] += 1
                pattern_to_types.setdefault(p, Counter())[et] += 1
                prev = pattern_last_ts.get(p)
                if not prev or ts > prev:
                    pattern_last_ts[p] = ts

        summaries: list[dict[str, Any]] = []
        for pattern_id, count in pattern_counts.most_common():
            if count < min_occurrences:
                continue
            types_counter = pattern_to_types.get(pattern_id, Counter())
            top_types = [name for name, _ in types_counter.most_common(5)]
            summaries.append(
                {
                    "pattern": pattern_id,
                    "occurrences": count,
                    "top_error_types": top_types,
                    "last_seen": pattern_last_ts.get(pattern_id, ""),
                }
            )
        return summaries

    def suggest_fixes(
        self,
        *,
        patterns: Sequence[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """
        Produce remediation bundles tailored for Cursor agents.

        If ``patterns`` is omitted, suggestions are driven by ``analyze_patterns()`` results.
        """

        if limit < 1:
            raise ValueError("limit must be at least 1.")

        if patterns is None:
            analyzed = self.analyze_patterns(min_occurrences=1)
            ordered = [str(item["pattern"]) for item in analyzed]
        else:
            ordered = [str(p) for p in patterns]

        suggestions: list[dict[str, Any]] = []
        seen: set[str] = set()

        for pid in ordered:
            if pid in seen:
                continue
            seen.add(pid)
            spec = _REMEDIATIONS.get(pid)
            if not spec:
                continue
            suggestions.append(
                {
                    "pattern": pid,
                    "headline": spec.headline,
                    "agent_prompt": spec.agent_prompt,
                }
            )
            if len(suggestions) >= limit:
                break

        if suggestions:
            return suggestions

        # Fallback: most recent error still receives a targeted prompt if no pattern matched storage.
        recent = self.get_recent_errors(limit=1)
        if not recent:
            return []

        row = recent[0]
        msg = str(row.get("message") or "")
        st = str(row.get("stack_trace") or "")
        inferred = detect_error_patterns(msg, st)
        if inferred:
            return self.suggest_fixes(patterns=inferred, limit=limit)

        et = str(row.get("error_type") or "Error")
        return [
            {
                "pattern": "generic",
                "headline": f"Investigate recent {et}",
                "agent_prompt": (
                    "You are a Cursor agent reviewing the latest captured error that did not match a built-in "
                    "pattern library.\n"
                    "Goals:\n"
                    "1. Reproduce or trace the failure using the stack trace and message below.\n"
                    "2. Identify whether the defect is configuration, environment, logic, or external dependency.\n"
                    "3. Implement the smallest correct fix and add regression coverage where practical.\n"
                    f"Recent error_type: {et}\n"
                    f"Message: {msg}\n"
                    "Use the repository's existing style, tests, and tooling; explain your reasoning briefly."
                ),
            }
        ]
