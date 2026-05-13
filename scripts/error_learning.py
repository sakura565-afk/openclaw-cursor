#!/usr/bin/env python3
"""Capture errors from logs, OpenClaw gateway output, and session transcripts.

Persists actionable learnings under ``.learnings/errors/`` as JSON records with
``timestamp``, ``error_type``, ``root_cause``, and ``fix_applied``. The legacy
JSON log at ``.learnings/error_log.json`` (category / error / lesson) remains
supported for ``add`` / ``list`` / ``stats`` / ``search`` and :func:`add_entry`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Final, Iterator

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = ROOT / ".learnings" / "error_log.json"
DEFAULT_ERRORS_DIR = ROOT / ".learnings" / "errors"
SCHEMA_VERSION = 1
ANSI: Final[dict[str, str]] = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}
FALLBACK_CATEGORY_COLORS: Final[tuple[str, ...]] = ("red", "yellow", "green")

MAX_SCAN_BYTES: Final[int] = 10 * 1024 * 1024
TRACEBACK_START = re.compile(r"^Traceback \(most recent call last\):")
LOG_SEVERITY = re.compile(r"\b(ERROR|CRITICAL|FATAL)\s*[:\-]?\s*(.+)$", re.IGNORECASE)
JSON_ERROR = re.compile(r'"error"\s*:\s*"([^"]+)"')
SESSION_ERROR_HINT = re.compile(
    r"(?i)(exception|traceback|error\s*:|failed\s+with|command\s+failed|non-zero\s+exit)"
)


class ErrorLearningError(RuntimeError):
    """Raised when the error learning log cannot be read or written."""


def resolve_openclaw_workspace() -> Path:
    """OpenClaw workspace root (``OPENCLAW_WORKSPACE`` or ``~/.openclaw/workspace``)."""

    override = os.environ.get("OPENCLAW_WORKSPACE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".openclaw" / "workspace").resolve()


def colorize(text: str, color: str) -> str:
    """Wrap text in ANSI color codes unless the user disabled them."""

    if os.environ.get("NO_COLOR"):
        return text
    prefix = ANSI.get(color, "")
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI['reset']}"


def normalize_text(text: str) -> str:
    """Normalize free-form text for comparisons and search."""

    return " ".join(text.strip().lower().split())


def category_color(category: str) -> str:
    """Choose a stable display color for a category name."""

    normalized = normalize_text(category)
    if any(token in normalized for token in ("lesson", "resolved", "fix", "success")):
        return "green"
    if any(token in normalized for token in ("warn", "warning", "caution")):
        return "yellow"
    if any(token in normalized for token in ("error", "failure", "fatal", "exception", "crash", "bug")):
        return "red"
    digest = hashlib.sha1(normalized.encode("utf-8")).digest()[0]
    return FALLBACK_CATEGORY_COLORS[digest % len(FALLBACK_CATEGORY_COLORS)]


def canonical_payload(category: str, error: str, lesson: str, resolved: bool) -> dict[str, object]:
    """Return a normalized payload used for IDs and deduplication."""

    return {
        "category": normalize_text(category),
        "error": normalize_text(error),
        "lesson": normalize_text(lesson),
        "resolved": bool(resolved),
    }


def build_entry(
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
    timestamp: str | None = None,
) -> dict[str, object]:
    """Create a log entry that matches the JSON schema."""

    payload = canonical_payload(category, error, lesson, resolved)
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    created_at = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "id": digest,
        "timestamp": created_at,
        "category": category.strip(),
        "error": error.strip(),
        "lesson": lesson.strip(),
        "resolved": bool(resolved),
    }


def default_store() -> dict[str, object]:
    """Return an empty log document."""

    return {"schema_version": SCHEMA_VERSION, "entries": []}


def validate_entry(raw_entry: object) -> dict[str, object]:
    """Validate a single persisted entry and normalize minor omissions."""

    if not isinstance(raw_entry, dict):
        raise ErrorLearningError("Each entry in the error log must be a JSON object.")

    entry = dict(raw_entry)
    required_text_fields = ("timestamp", "category", "error", "lesson")
    for field in required_text_fields:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ErrorLearningError(f"Entry field '{field}' must be a non-empty string.")

    resolved = entry.get("resolved", False)
    if not isinstance(resolved, bool):
        raise ErrorLearningError("Entry field 'resolved' must be a boolean.")

    if not isinstance(entry.get("id"), str) or not entry["id"].strip():
        entry["id"] = build_entry(
            entry["category"],
            entry["error"],
            entry["lesson"],
            resolved=resolved,
            timestamp=entry["timestamp"],
        )["id"]
    entry["resolved"] = resolved
    return entry


def load_store(log_path: Path) -> dict[str, object]:
    """Load the persisted error log from disk."""

    if not log_path.exists():
        return default_store()

    try:
        raw = json.loads(log_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErrorLearningError(f"Unable to parse JSON from {log_path}: {exc}") from exc

    if isinstance(raw, list):
        entries = [validate_entry(item) for item in raw]
        return {"schema_version": SCHEMA_VERSION, "entries": entries}

    if not isinstance(raw, dict):
        raise ErrorLearningError("Error log must contain a JSON object or list of entries.")

    raw_entries = raw.get("entries", [])
    if not isinstance(raw_entries, list):
        raise ErrorLearningError("Error log field 'entries' must be a JSON array.")

    return {
        "schema_version": int(raw.get("schema_version", SCHEMA_VERSION)),
        "entries": [validate_entry(item) for item in raw_entries],
    }


def save_store(log_path: Path, store: dict[str, object]) -> None:
    """Persist the error log to disk."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")


def entries_match(left: dict[str, object], right: dict[str, object]) -> bool:
    """Return True when two entries are the same learning."""

    if left.get("id") == right.get("id"):
        return True
    return canonical_payload(
        str(left["category"]),
        str(left["error"]),
        str(left["lesson"]),
        bool(left["resolved"]),
    ) == canonical_payload(
        str(right["category"]),
        str(right["error"]),
        str(right["lesson"]),
        bool(right["resolved"]),
    )


def add_entry(
    log_path: Path,
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
) -> tuple[dict[str, object], bool]:
    """Add an error learning entry unless it already exists."""

    store = load_store(log_path)
    new_entry = build_entry(category, error, lesson, resolved=resolved)
    entries = store["entries"]
    assert isinstance(entries, list)
    for entry in entries:
        validated = validate_entry(entry)
        if entries_match(validated, new_entry):
            return validated, False

    entries.append(new_entry)
    entries.sort(key=lambda item: str(item["timestamp"]), reverse=True)
    save_store(log_path, store)
    return new_entry, True


def format_entry(entry: dict[str, object]) -> str:
    """Render a single entry for console output."""

    category = str(entry["category"])
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    lines = [
        (
            f"{colorize(category, category_color(category))} "
            f"{colorize(f'[{status_text}]', status_color)} "
            f"{colorize(str(entry['timestamp']), 'cyan')}"
        ),
        f"  {colorize('ID:', 'yellow')} {entry['id']}",
        f"  {colorize('Error:', 'red')} {entry['error']}",
        f"  {colorize('Lesson:', 'green')} {entry['lesson']}",
    ]
    return "\n".join(lines)


def print_entries(entries: list[dict[str, object]], *, heading: str) -> None:
    """Print a collection of entries in a human-readable layout."""

    print(colorize(heading, "bold"))
    print(colorize("=" * len(heading), "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    for index, entry in enumerate(entries):
        if index:
            print()
        print(format_entry(entry))


def print_stats(entries: list[dict[str, object]]) -> None:
    """Print category-level frequency stats."""

    print(colorize("Error Learning Stats", "bold"))
    print(colorize("====================", "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    counts = Counter(str(entry["category"]) for entry in entries)
    total = len(entries)
    for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (count / total) * 100
        print(
            f"- {colorize(category, category_color(category))}: "
            f"{colorize(str(count), 'red')} "
            f"({share:.1f}%)"
        )


def search_score(query: str, entry: dict[str, object]) -> float:
    """Score how relevant an entry is to a search query."""

    normalized_query = normalize_text(query)
    haystack = normalize_text(
        " ".join((str(entry["category"]), str(entry["error"]), str(entry["lesson"])))
    )
    if not normalized_query:
        return 0.0

    substring_bonus = 1.5 if normalized_query in haystack else 0.0
    query_tokens = set(normalized_query.split())
    haystack_tokens = set(haystack.split())
    overlap = len(query_tokens & haystack_tokens) / max(len(query_tokens), 1)
    ratio = SequenceMatcher(None, normalized_query, haystack).ratio()
    return substring_bonus + overlap + (ratio * 0.5)


def search_entries(entries: list[dict[str, object]], query: str, limit: int = 10) -> list[dict[str, object]]:
    """Return the most relevant matching entries for the given query."""

    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = search_score(query, entry)
        if score >= 0.45:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])), reverse=False)
    return [entry for _, entry in ranked[:limit]]


# ---------------------------------------------------------------------------
# Actionable learnings (.learnings/errors/*.json)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionableLearning:
    """Structured learning written under ``.learnings/errors/``."""

    timestamp: str
    error_type: str
    root_cause: str
    fix_applied: str
    source: str
    source_path: str
    raw_excerpt: str

    def content_fingerprint(self) -> str:
        payload = f"{self.error_type}\n{self.root_cause}\n{self.raw_excerpt[:2000]}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_json_dict(self) -> dict[str, str]:
        d = asdict(self)
        d["fingerprint"] = self.content_fingerprint()
        return d


def _utc_timestamp_file() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def _slug(text: str, max_len: int = 48) -> str:
    s = re.sub(r"[^\w\-]+", "_", text.strip())[:max_len].strip("_")
    return s or "unknown"


def _read_scan_text(path: Path) -> str | None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        logger.warning("Cannot stat %s: %s", path, exc)
        return None
    if size > MAX_SCAN_BYTES:
        logger.info("Skipping large file %s (%s bytes)", path, size)
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Cannot read %s: %s", path, exc)
        return None


def _default_fix(error_type: str, root_cause: str) -> str:
    low = root_cause.lower()
    if "traceback" in low or "exception" in error_type.lower():
        return "Inspect the stack frame, reproduce locally, add a regression test, then patch the failing call."
    if "permission" in low or "eacces" in low or "eperm" in low:
        return "Verify file ownership and mode; run with least privilege needed for the resource."
    if "enonet" in low or "econnrefused" in low or "timeout" in low:
        return "Check network reachability, firewall rules, and retry with backoff; confirm the remote service is up."
    if "json" in low and ("parse" in low or "decode" in low or "invalid" in low):
        return "Validate JSON with a strict schema before parsing; handle partial payloads and log raw bytes length."
    if "401" in root_cause or "403" in root_cause or "unauthorized" in low:
        return "Refresh credentials or tokens; confirm API scopes and clock skew."
    return "Review the excerpt in context, narrow to a minimal repro, then apply a targeted fix and document the failure mode."


def _iter_traceback_blocks(text: str) -> Iterator[str]:
    lines = text.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        if not TRACEBACK_START.match(lines[i]):
            i += 1
            continue
        start = i
        i += 1
        while i < n and not TRACEBACK_START.match(lines[i]):
            i += 1
        yield "\n".join(lines[start:i])


def _repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _parse_traceback(block: str) -> tuple[str, str] | None:
    lines = [ln for ln in block.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    last = lines[-1]
    if ":" in last:
        exc_name, msg = last.split(":", 1)
        exc_name = exc_name.strip()
        msg = msg.strip() or "(no message)"
    else:
        exc_name, msg = "Exception", last.strip()
    return exc_name, f"{exc_name}: {msg}"


def extract_from_log_text(text: str, source_label: str, source_path: str) -> list[ActionableLearning]:
    """Heuristic extraction of errors from plain log text."""

    out: list[ActionableLearning] = []
    ts = _utc_timestamp_file()

    for block in _iter_traceback_blocks(text):
        parsed = _parse_traceback(block)
        if not parsed:
            continue
        exc_name, root = parsed
        out.append(
            ActionableLearning(
                timestamp=ts,
                error_type=f"python_traceback:{exc_name}",
                root_cause=root[:4000],
                fix_applied=_default_fix(exc_name, root),
                source=source_label,
                source_path=source_path,
                raw_excerpt=block[:8000],
            )
        )

    for match in LOG_SEVERITY.finditer(text):
        sev, msg = match.group(1), match.group(2).strip()
        if len(msg) < 8:
            continue
        out.append(
            ActionableLearning(
                timestamp=ts,
                error_type=f"log_{sev.lower()}",
                root_cause=msg[:4000],
                fix_applied=_default_fix(sev, msg),
                source=source_label,
                source_path=source_path,
                raw_excerpt=msg[:2000],
            )
        )

    for match in JSON_ERROR.finditer(text):
        msg = match.group(1).strip()
        if len(msg) < 4:
            continue
        out.append(
            ActionableLearning(
                timestamp=ts,
                error_type="json_api_error",
                root_cause=msg[:4000],
                fix_applied=_default_fix("json_api_error", msg),
                source=source_label,
                source_path=source_path,
                raw_excerpt=msg[:2000],
            )
        )

    return out


def _collect_log_file_paths(extra_dirs: list[Path], workspace: Path) -> list[Path]:
    roots: list[Path] = [ROOT / "logs", workspace / "memory"]
    roots.extend(extra_dirs)
    seen: set[Path] = set()
    files: list[Path] = []
    for root in roots:
        root = root.expanduser().resolve()
        if not root.exists():
            logger.debug("Log root missing: %s", root)
            continue
        if root.is_file():
            candidates = [root]
        else:
            candidates = sorted(root.rglob("*.log")) + sorted(root.rglob("*.txt"))
        for path in candidates:
            rp = path.resolve()
            if rp in seen or not path.is_file():
                continue
            seen.add(rp)
            files.append(path)
    return files


def _collect_gateway_paths(workspace: Path) -> list[Path]:
    """Paths that commonly hold OpenClaw gateway stderr or daemon logs."""

    patterns = ("*gateway*.log", "*gateway*.txt", "gateway.log", "openclaw-gateway.log")
    found: list[Path] = []
    seen: set[Path] = set()
    for base in (workspace, workspace.parent, Path.home() / ".openclaw"):
        b = base.expanduser().resolve()
        if not b.exists():
            continue
        for pat in patterns:
            for path in b.glob(pat):
                if path.is_file():
                    rp = path.resolve()
                    if rp not in seen:
                        seen.add(rp)
                        found.append(path)
            for path in b.rglob(pat):
                if path.is_file() and len(path.relative_to(b).parts) <= 6:
                    rp = path.resolve()
                    if rp not in seen:
                        seen.add(rp)
                        found.append(path)
    return found


def _collect_session_paths(workspace: Path, extra_dirs: list[Path]) -> list[Path]:
    bases = [workspace, ROOT / "memory", *extra_dirs]
    globs = ("**/session.json", "**/sessions/**/*.json", "**/transcript*.json", "**/*session*.json")
    seen: set[Path] = set()
    out: list[Path] = []
    for base in bases:
        b = base.expanduser().resolve()
        if not b.is_dir():
            continue
        for pattern in globs:
            try:
                for path in b.glob(pattern):
                    if path.is_file() and path.suffix.lower() == ".json":
                        rp = path.resolve()
                        if rp not in seen:
                            seen.add(rp)
                            out.append(path)
            except OSError as exc:
                logger.debug("Glob failed under %s: %s", b, exc)
    return out


def _flatten_json_strings(obj: Any, max_depth: int = 12) -> Iterator[str]:
    if max_depth <= 0:
        return
    if isinstance(obj, str):
        if len(obj.strip()) > 20:
            yield obj
        return
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _flatten_json_strings(v, max_depth - 1)
        return
    if isinstance(obj, list):
        for item in obj:
            yield from _flatten_json_strings(item, max_depth - 1)


def extract_from_session_json(text: str, source_path: str) -> list[ActionableLearning]:
    """Pull assistant/user strings that look like failures from transcript JSON."""

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    ts = _utc_timestamp_file()
    out: list[ActionableLearning] = []
    for blob in _flatten_json_strings(data):
        if not SESSION_ERROR_HINT.search(blob):
            continue
        snippet = blob.strip()
        if len(snippet) < 40:
            continue
        head = snippet[:200].replace("\n", " ")
        out.append(
            ActionableLearning(
                timestamp=ts,
                error_type="session_transcript_error_signal",
                root_cause=head[:4000],
                fix_applied=_default_fix("session", snippet),
                source="session",
                source_path=source_path,
                raw_excerpt=snippet[:8000],
            )
        )
    return out


def _existing_fingerprints(errors_dir: Path) -> set[str]:
    fps: set[str] = set()
    if not errors_dir.is_dir():
        return fps
    for path in errors_dir.glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        fp = raw.get("fingerprint")
        if isinstance(fp, str):
            fps.add(fp)
    return fps


def persist_learnings(learnings: list[ActionableLearning], errors_dir: Path) -> int:
    """Write new learning files; skip duplicates by fingerprint. Returns count written."""

    errors_dir.mkdir(parents=True, exist_ok=True)
    known = _existing_fingerprints(errors_dir)
    written = 0
    for item in learnings:
        fp = item.content_fingerprint()
        if fp in known:
            logger.debug("Duplicate learning skipped fingerprint=%s", fp)
            continue
        known.add(fp)
        stem = f"{item.timestamp}_{_slug(item.error_type)}_{fp[:8]}"
        path = errors_dir / f"{stem}.json"
        payload = item.to_json_dict()
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        logger.info("Wrote learning %s", path.name)
        written += 1
    return written


def scan_logs(
    errors_dir: Path,
    workspace: Path,
    *,
    extra_log_dirs: list[Path] | None = None,
) -> int:
    """Scan repository logs, OpenClaw memory logs, and gateway-style log files."""

    extra = list(extra_log_dirs or [])
    paths = _collect_log_file_paths(extra, workspace)
    gw_paths = _collect_gateway_paths(workspace)
    gateway_resolved = {p.resolve() for p in gw_paths}
    seen: set[Path] = {p.resolve() for p in paths}
    for gw in gw_paths:
        r = gw.resolve()
        if r not in seen:
            seen.add(r)
            paths.append(gw)
    logger.info("Scanning %d log / gateway files for errors", len(paths))
    batch: list[ActionableLearning] = []
    for path in paths:
        text = _read_scan_text(path)
        if not text:
            continue
        rel = _repo_relative(path)
        label = "gateway" if path.resolve() in gateway_resolved else "log"
        batch.extend(extract_from_log_text(text, label, rel))
    return persist_learnings(batch, errors_dir)


def scan_gateway_output(errors_dir: Path, workspace: Path) -> int:
    """Scan likely OpenClaw gateway log locations (included in log scan heuristics)."""

    paths = _collect_gateway_paths(workspace)
    logger.info("Scanning %d gateway-related files", len(paths))
    batch: list[ActionableLearning] = []
    for path in paths:
        text = _read_scan_text(path)
        if not text:
            continue
        rel = path.as_posix()
        batch.extend(extract_from_log_text(text, "gateway", rel))
    return persist_learnings(batch, errors_dir)


def scan_sessions(
    errors_dir: Path,
    workspace: Path,
    *,
    extra_session_dirs: list[Path] | None = None,
) -> int:
    """Scan session / transcript JSON under workspace and repo memory."""

    extra = list(extra_session_dirs or [])
    paths = _collect_session_paths(workspace, extra)
    logger.info("Scanning %d session JSON files", len(paths))
    batch: list[ActionableLearning] = []
    for path in paths:
        text = _read_scan_text(path)
        if not text:
            continue
        rel = _repo_relative(path)
        batch.extend(extract_from_session_json(text, rel))
    return persist_learnings(batch, errors_dir)


def load_actionable_learnings(errors_dir: Path) -> list[dict[str, Any]]:
    """Load all actionable learning JSON objects from ``errors_dir``."""

    if not errors_dir.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(errors_dir.glob("*.json"), key=lambda p: p.name, reverse=True):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skip unreadable learning file %s: %s", path, exc)
    return rows


def format_actionable_learning(row: dict[str, Any]) -> str:
    """Human-readable block for one actionable learning."""

    ts = str(row.get("timestamp", ""))
    et = str(row.get("error_type", ""))
    lines = [
        f"{colorize(et, category_color(et))} {colorize(ts, 'cyan')}",
        f"  {colorize('source:', 'yellow')} {row.get('source', '')} {row.get('source_path', '')}",
        f"  {colorize('root_cause:', 'red')} {row.get('root_cause', '')}",
        f"  {colorize('fix_applied:', 'green')} {row.get('fix_applied', '')}",
    ]
    return "\n".join(lines)


def show_actionable_learnings(errors_dir: Path) -> None:
    """Print actionable learnings from disk."""

    rows = load_actionable_learnings(errors_dir)
    title = f"Actionable error learnings ({errors_dir})"
    print(colorize(title, "bold"))
    print(colorize("=" * len(title), "cyan"))
    if not rows:
        print(colorize("No actionable learning files found.", "yellow"))
        return
    for i, row in enumerate(rows):
        if i:
            print()
        print(format_actionable_learning(row))


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Capture and learn from OpenClaw errors (legacy log + actionable files).",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to the legacy error learning JSON log.",
    )
    parser.add_argument(
        "--errors-dir",
        type=Path,
        default=DEFAULT_ERRORS_DIR,
        help="Directory for actionable learning JSON files.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="OpenClaw workspace root (default: OPENCLAW_WORKSPACE or ~/.openclaw/workspace).",
    )
    parser.add_argument(
        "--extra-log-dir",
        type=Path,
        action="append",
        default=[],
        metavar="DIR",
        help="Additional directory to scan for *.log / *.txt (repeatable).",
    )
    parser.add_argument(
        "--extra-session-dir",
        type=Path,
        action="append",
        default=[],
        metavar="DIR",
        help="Extra base directory for session JSON discovery (repeatable).",
    )
    parser.add_argument(
        "--scan-logs",
        action="store_true",
        help="Scan logs (repo, workspace memory) plus OpenClaw gateway log paths; write learnings.",
    )
    parser.add_argument(
        "--scan-sessions",
        action="store_true",
        help="Scan session / transcript JSON and store learnings under --errors-dir.",
    )
    parser.add_argument("--show-learnings", action="store_true", help="Print actionable learnings from --errors-dir.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging on stderr.")

    subparsers = parser.add_subparsers(dest="command", required=False)

    add_parser = subparsers.add_parser("add", help="Add a legacy JSON log entry.")
    add_parser.add_argument("error_category", help="High-level category for the error.")
    add_parser.add_argument("error_message", help="Error message or failure summary.")
    add_parser.add_argument("lesson_learned", help="Lesson learned from the failure.")
    resolved_group = add_parser.add_mutually_exclusive_group()
    resolved_group.add_argument(
        "--resolved",
        dest="resolved",
        action="store_true",
        default=True,
        help="Mark the entry as resolved (default).",
    )
    resolved_group.add_argument(
        "--unresolved",
        dest="resolved",
        action="store_false",
        help="Mark the entry as still open.",
    )

    subparsers.add_parser("list", help="List all legacy learned errors.")
    subparsers.add_parser("stats", help="Show legacy error frequency by category.")

    search_parser = subparsers.add_parser("search", help="Search legacy log for relevant past errors.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of matching entries to print.",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the error learning CLI."""

    args = parse_args(argv)
    configure_logging(args.verbose)

    workspace = (args.workspace or resolve_openclaw_workspace()).expanduser().resolve()
    errors_dir = args.errors_dir.expanduser().resolve()

    scan_any = args.scan_logs or args.scan_sessions or args.show_learnings
    exit_code = 0

    if args.scan_logs:
        n = scan_logs(errors_dir, workspace, extra_log_dirs=args.extra_log_dir)
        print(f"Scan logs: wrote {n} new actionable learning file(s) under {errors_dir}", file=sys.stderr)
    if args.scan_sessions:
        n = scan_sessions(errors_dir, workspace, extra_session_dirs=args.extra_session_dir)
        print(f"Scan sessions: wrote {n} new actionable learning file(s) under {errors_dir}", file=sys.stderr)
    if args.show_learnings:
        show_actionable_learnings(errors_dir)

    if args.command:
        try:
            store = load_store(args.log_path)
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1

        entries = store["entries"]
        assert isinstance(entries, list)

        if args.command == "add":
            try:
                entry, created = add_entry(
                    args.log_path,
                    args.error_category,
                    args.error_message,
                    args.lesson_learned,
                    resolved=args.resolved,
                )
            except ErrorLearningError as exc:
                print(colorize(str(exc), "red"), file=sys.stderr)
                return 1

            if created:
                print(colorize("Saved error learning entry.", "green"))
            else:
                print(colorize("Duplicate entry detected; existing learning kept.", "yellow"))
            print(format_entry(entry))
            return exit_code

        validated_entries = [validate_entry(entry) for entry in entries]

        if args.command == "list":
            print_entries(validated_entries, heading="OpenClaw Error Learnings")
            return exit_code

        if args.command == "stats":
            print_stats(validated_entries)
            return exit_code

        if args.command == "search":
            matches = search_entries(validated_entries, args.query, limit=max(args.limit, 1))
            print_entries(matches, heading=f"Search Results: {args.query}")
            return exit_code

        print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
        return 1

    if not scan_any and not args.command:
        print(
            colorize(
                "Specify --scan-logs, --scan-sessions, --show-learnings, "
                "or a legacy subcommand (add, list, stats, search).",
                "yellow",
            ),
            file=sys.stderr,
        )
        return 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
