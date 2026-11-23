"""Iskra (tasks bot) → Kara (main agent) shared queue on disk.

A JSON queue under the OpenClaw workspace replaces ad-hoc scanning of
``tasks/results/`` for cron-driven handoff. Writers append under a file lock;
Kara drains atomically and clears the queue. If the queue file is corrupt or
unreadable, callers can fall back to ``tasks/results/`` (see
``collect_fallback_tasks_results``).
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from src.coordination.cross_bot_sync import FileLock, LockTimeoutError, atomic_write_json, utc_now

QUEUE_VERSION = 1
RESULTS_BASENAME = "iskra_kara_results.json"
FALLBACK_STATE_BASENAME = "iskra_kara_fallback_state.json"
TASKS_RESULTS_SUBPATH = Path("tasks") / "results"
TEXT_SUFFIXES = {".md", ".json", ".txt", ".log"}


def resolve_openclaw_workspace() -> Path:
    """Workspace root (``~/.openclaw/workspace`` by default)."""

    override = os.environ.get("OPENCLAW_WORKSPACE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".openclaw" / "workspace").resolve()


def default_shared_memory_dir(workspace: Optional[Path] = None) -> Path:
    root = workspace if workspace is not None else resolve_openclaw_workspace()
    return root / "shared_memory"


def default_results_path(workspace: Optional[Path] = None) -> Path:
    return default_shared_memory_dir(workspace) / RESULTS_BASENAME


def default_lock_path(results_path: Path) -> Path:
    return results_path.with_name(results_path.name + ".lock")


def default_fallback_state_path(workspace: Optional[Path] = None) -> Path:
    return default_shared_memory_dir(workspace) / FALLBACK_STATE_BASENAME


def new_empty_queue_document() -> Dict[str, Any]:
    return {
        "version": QUEUE_VERSION,
        "updated_at": utc_now(),
        "entries": [],
    }


def _normalize_queue_document(raw: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return (entries, full_doc) or raise ValueError if the payload is unusable."""

    if isinstance(raw, list):
        doc = new_empty_queue_document()
        doc["entries"] = [e for e in raw if isinstance(e, dict)]
        return doc["entries"], doc
    if not isinstance(raw, dict):
        raise ValueError("queue root must be a JSON object or array")
    entries = raw.get("entries")
    if entries is None:
        raise ValueError("queue object missing 'entries'")
    if not isinstance(entries, list):
        raise ValueError("'entries' must be a list")
    cleaned: List[Dict[str, Any]] = []
    for item in entries:
        if isinstance(item, dict):
            cleaned.append(item)
    out = {
        "version": int(raw.get("version", QUEUE_VERSION)),
        "updated_at": utc_now(),
        "entries": cleaned,
    }
    return cleaned, out


def _read_queue_file(path: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not path.exists():
        return [], new_empty_queue_document()
    text = path.read_text(encoding="utf-8")
    raw = json.loads(text)
    entries, doc = _normalize_queue_document(raw)
    return entries, doc


def append_iskra_result(
    kind: str,
    payload: Dict[str, Any],
    *,
    results_path: Optional[Path] = None,
    lock_timeout: float = 30.0,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Append one queue entry (Iskra writer). Returns the stored entry."""

    path = Path(results_path or default_results_path())
    lock_path = default_lock_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    entry: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "source_bot": "iskra",
        "created_at": utc_now(),
        "payload": dict(payload),
    }
    if extra:
        entry.update({k: v for k, v in extra.items() if k not in entry})

    with FileLock(lock_path, timeout=lock_timeout):
        try:
            _, doc = _read_queue_file(path)
        except (json.JSONDecodeError, OSError, UnicodeError, ValueError):
            doc = new_empty_queue_document()
        doc["entries"].append(entry)
        doc["updated_at"] = utc_now()
        atomic_write_json(path, doc)
    return entry


def notify_kara_from_iskra(
    kind: str,
    payload: Dict[str, Any],
    *,
    results_path: Optional[Path] = None,
    lock_timeout: float = 30.0,
) -> bool:
    """Best-effort append for scripts; returns False on failure (never raises)."""

    try:
        append_iskra_result(kind, payload, results_path=results_path, lock_timeout=lock_timeout)
        return True
    except Exception:
        return False


DrainStatus = Literal["drained", "empty", "corrupt", "missing", "inaccessible"]


def drain_shared_memory_entries(
    *,
    results_path: Optional[Path] = None,
    lock_timeout: float = 30.0,
) -> Tuple[List[Dict[str, Any]], DrainStatus]:
    """Pop all entries under lock and reset the queue. Returns (entries, status)."""

    path = Path(results_path or default_results_path())
    lock_path = default_lock_path(path)

    if not path.exists():
        return [], "missing"

    try:
        with FileLock(lock_path, timeout=lock_timeout):
            try:
                entries, _ = _read_queue_file(path)
            except (json.JSONDecodeError, OSError, UnicodeError, ValueError):
                return [], "corrupt"

            if not entries:
                atomic_write_json(path, new_empty_queue_document())
                return [], "empty"

            atomic_write_json(path, new_empty_queue_document())
            return list(entries), "drained"
    except LockTimeoutError:
        return [], "inaccessible"


def _load_fallback_state(state_path: Path) -> Dict[str, Any]:
    if not state_path.exists():
        return {"processed": {}}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return {"processed": {}}
    if not isinstance(data, dict):
        return {"processed": {}}
    proc = data.get("processed")
    if not isinstance(proc, dict):
        data["processed"] = {}
    return data


def _save_fallback_state(state_path: Path, data: Dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(state_path, data)


def collect_fallback_tasks_results(
    workspace: Optional[Path] = None,
    *,
    state_path: Optional[Path] = None,
    max_bytes_per_file: int = 256_000,
    lock_timeout: float = 10.0,
) -> Tuple[List[Dict[str, Any]], Path]:
    """Scan ``<workspace>/tasks/results`` for new text/json files since last run.

    Returns synthetic queue entries ``kind == "tasks_result_file"`` and the
    state path used. Tracks processed files by relative path and mtime in
    ``iskra_kara_fallback_state.json``.
    """

    root = workspace if workspace is not None else resolve_openclaw_workspace()
    results_dir = root / TASKS_RESULTS_SUBPATH
    fb_path = Path(state_path or default_fallback_state_path(root))
    lock_path = fb_path.with_name(fb_path.name + ".lock")

    synthetic: List[Dict[str, Any]] = []
    if not results_dir.is_dir():
        return synthetic, fb_path

    with FileLock(lock_path, timeout=lock_timeout):
        state = _load_fallback_state(fb_path)
        processed: Dict[str, Any] = state.setdefault("processed", {})
        assert isinstance(processed, dict)

        candidates: List[Path] = []
        for path in sorted(results_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            candidates.append(path)

        for path in sorted(candidates, key=lambda p: p.stat().st_mtime_ns):
            try:
                rel = path.relative_to(root).as_posix()
                mtime = path.stat().st_mtime
            except (OSError, ValueError):
                continue

            prev = processed.get(rel)
            if prev is not None and float(prev) >= mtime:
                continue

            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            if len(raw) > max_bytes_per_file:
                raw = raw[: max_bytes_per_file - 80] + "\n\n[truncated for Kara sync]\n"

            synthetic.append(
                {
                    "id": str(uuid.uuid4()),
                    "kind": "tasks_result_file",
                    "source_bot": "iskra",
                    "created_at": utc_now(),
                    "payload": {
                        "relative_path": rel,
                        "content": raw,
                        "_file_mtime": mtime,
                    },
                }
            )

    return synthetic, fb_path


def commit_fallback_consumed(
    entries: List[Dict[str, Any]],
    workspace: Optional[Path] = None,
    *,
    state_path: Optional[Path] = None,
    lock_timeout: float = 10.0,
) -> None:
    """Record ``tasks_result_file`` entries as delivered (call after Kara output is emitted)."""

    if not entries:
        return
    root = workspace if workspace is not None else resolve_openclaw_workspace()
    fb_path = Path(state_path or default_fallback_state_path(root))
    lock_path = fb_path.with_name(fb_path.name + ".lock")

    with FileLock(lock_path, timeout=lock_timeout):
        state = _load_fallback_state(fb_path)
        processed: Dict[str, Any] = state.setdefault("processed", {})
        assert isinstance(processed, dict)
        for entry in entries:
            if entry.get("kind") != "tasks_result_file":
                continue
            payload = entry.get("payload") or {}
            rel = payload.get("relative_path")
            if not isinstance(rel, str):
                continue
            mtime_val = payload.get("_file_mtime")
            if isinstance(mtime_val, (int, float)):
                processed[rel] = float(mtime_val)
                continue
            target = (root / rel).resolve()
            try:
                if target.is_file():
                    processed[rel] = target.stat().st_mtime
            except OSError:
                processed[rel] = float(processed.get(rel, 0.0))
        state["updated_at"] = utc_now()
        _save_fallback_state(fb_path, state)


def format_kara_message(entries: List[Dict[str, Any]]) -> str:
    """Human-readable batch for the Kara proxy message body."""

    lines: List[str] = ["# Iskra → Kara", "", f"_Items: {len(entries)}_", ""]
    for idx, entry in enumerate(entries, start=1):
        kind = str(entry.get("kind", "unknown"))
        eid = str(entry.get("id", ""))
        lines.append(f"## {idx}. `{kind}` ({eid[:8]}…)" if len(eid) > 8 else f"## {idx}. `{kind}`")
        payload = entry.get("payload")
        if isinstance(payload, dict):
            if "summary_markdown" in payload and isinstance(payload["summary_markdown"], str):
                lines.append(payload["summary_markdown"].strip())
            elif "content" in payload and isinstance(payload["content"], str):
                rel = payload.get("relative_path", "file")
                lines.append(f"_Source: {rel}_\n")
                lines.append("```")
                lines.append(payload["content"].strip())
                lines.append("```")
            else:
                public = {k: v for k, v in payload.items() if not str(k).startswith("_")}
                lines.append("```json")
                lines.append(json.dumps(public, indent=2, ensure_ascii=False))
                lines.append("```")
        else:
            lines.append(str(payload))
        lines.append("")
    return "\n".join(lines).strip() + "\n"
