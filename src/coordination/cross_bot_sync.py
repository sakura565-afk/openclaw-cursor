"""Cross-bot coordination helpers for OpenClaw bots.

This module keeps a lightweight shared state so multiple bots can:

* synchronize ``MEMORY.md`` entries,
* claim or hand off tasks without duplicating work,
* publish their current status, and
* coordinate concurrent access through a shared lock file.

The implementation prefers stdlib primitives. If ``filelock`` is available in
the environment it will be used, otherwise a small ``O_EXCL``-based lock file
implementation is used as a fallback.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, Iterable, Optional

try:  # pragma: no cover - exercised indirectly when dependency exists.
    from filelock import FileLock as _ExternalFileLock  # type: ignore
    from filelock import Timeout as _ExternalTimeout  # type: ignore
except ImportError:  # pragma: no cover - primary path in this repo.
    _ExternalFileLock = None
    _ExternalTimeout = TimeoutError


DEFAULT_SHARED_DIR = Path.home() / ".openclaw" / "shared"
DEFAULT_STATE_FILE = DEFAULT_SHARED_DIR / "state.json"
DEFAULT_STATUS_FILE = DEFAULT_SHARED_DIR / "bot_status.json"
DEFAULT_LOCK_FILE = DEFAULT_SHARED_DIR / "state.lock"
DEFAULT_MEMORY_FILE = Path.cwd() / "MEMORY.md"


def utc_now() -> str:
    """Return an ISO 8601 timestamp in UTC."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_task(task: str) -> str:
    """Normalize task descriptions so equivalent claims share a key."""

    return re.sub(r"\s+", " ", task.strip()).lower()


def normalize_memory_key(line: str) -> str:
    """Normalize a memory line for de-duplication and conflict resolution."""

    cleaned = re.sub(r"^[-*+]\s+", "", line.strip())
    if ":" in cleaned:
        cleaned = cleaned.split(":", 1)[0]
    return re.sub(r"\s+", " ", cleaned).lower()


def parse_memory_entries(text: str) -> Dict[str, str]:
    """Convert markdown text into normalized memory entries."""

    entries: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key = normalize_memory_key(line)
        if key:
            entries[key] = line
    return entries


def render_memory(entries: Dict[str, Dict[str, Any]]) -> str:
    """Render normalized memory entries back to a deterministic markdown file."""

    sorted_entries = sorted(
        entries.values(),
        key=lambda item: (item.get("updated_at", ""), item.get("line", "").lower()),
    )
    lines = [item["line"] for item in sorted_entries if item.get("line")]
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Read a JSON file and return a mapping, or a default on missing files."""

    if not path.exists():
        return {} if default is None else default.copy()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        return data
    raise ValueError(f"Expected JSON object in {path}")


def atomic_write_text(path: Path, content: str) -> None:
    """Atomically write text content to a file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(content)
        temp_path = Path(tmp.name)
    temp_path.replace(path)


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Atomically write JSON content to a file."""

    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


class FallbackFileLock:
    """A small lock-file implementation using ``O_EXCL`` semantics."""

    def __init__(
        self,
        lock_path: Path,
        timeout: float = 10.0,
        poll_interval: float = 0.1,
        stale_seconds: float = 300.0,
    ) -> None:
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.stale_seconds = stale_seconds
        self._acquired = False

    def _is_stale(self) -> bool:
        if not self.lock_path.exists():
            return False
        try:
            age = time.time() - self.lock_path.stat().st_mtime
        except FileNotFoundError:
            return False
        return age >= self.stale_seconds

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        while True:
            try:
                descriptor = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644,
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "created_at": utc_now(),
                            "pid": os.getpid(),
                        },
                        handle,
                    )
                self._acquired = True
                return
            except FileExistsError:
                if self._is_stale():
                    try:
                        self.lock_path.unlink()
                        continue
                    except FileNotFoundError:
                        continue
                if self.timeout is not None and time.monotonic() - start >= self.timeout:
                    raise TimeoutError(f"Timed out waiting for lock: {self.lock_path}")
                time.sleep(self.poll_interval)

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass
        finally:
            self._acquired = False

    def __enter__(self) -> "FallbackFileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


class LockTimeoutError(TimeoutError):
    """Raised when the shared lock cannot be acquired in time."""


class FileLock:
    """Thin wrapper that normalizes lock handling across implementations."""

    def __init__(self, lock_path: Path, timeout: float = 10.0) -> None:
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        self._lock = (
            _ExternalFileLock(str(self.lock_path), timeout=timeout)
            if _ExternalFileLock is not None
            else FallbackFileLock(self.lock_path, timeout=timeout)
        )

    def acquire(self) -> None:
        try:
            self._lock.acquire()
        except _ExternalTimeout as exc:  # pragma: no cover - depends on optional package.
            raise LockTimeoutError(str(exc)) from exc
        except TimeoutError as exc:
            raise LockTimeoutError(str(exc)) from exc

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def build_lock(lock_path: Path, timeout: float = 10.0):
    """Create either an external ``filelock`` instance or the fallback lock."""

    return FileLock(lock_path, timeout=timeout)


@dataclass
class CrossBotSyncCoordinator:
    """Coordinator for shared bot state, memory sync, and status writes."""

    shared_dir: Path = DEFAULT_SHARED_DIR
    state_file: Path = DEFAULT_STATE_FILE
    status_file: Path = DEFAULT_STATUS_FILE
    lock_file: Path = DEFAULT_LOCK_FILE
    lock_timeout: float = 10.0

    def __init__(
        self,
        shared_dir: Optional[Path] = None,
        state_file: Optional[Path] = None,
        status_file: Optional[Path] = None,
        lock_file: Optional[Path] = None,
        lock_timeout: float = 10.0,
    ) -> None:
        resolved_shared_dir = Path(shared_dir or os.environ.get("OPENCLAW_SHARED_DIR", DEFAULT_SHARED_DIR))
        self.shared_dir = resolved_shared_dir
        self.state_file = Path(state_file or resolved_shared_dir / "state.json")
        self.status_file = Path(status_file or resolved_shared_dir / "bot_status.json")
        self.lock_file = Path(lock_file or resolved_shared_dir / "state.lock")
        self.lock_timeout = lock_timeout

    def _ensure_shared_dir(self) -> None:
        self.shared_dir.mkdir(parents=True, exist_ok=True)

    def _lock(self):
        self._ensure_shared_dir()
        return build_lock(self.lock_file, timeout=self.lock_timeout)

    def _read_state(self) -> Dict[str, Any]:
        return read_json(self.state_file, default={"memory": {"entries": {}, "bots": {}}, "tasks": {}})

    def _write_state(self, state: Dict[str, Any]) -> None:
        atomic_write_json(self.state_file, state)

    def unlock(self) -> bool:
        """Remove the shared lock file if present."""

        if self.lock_file.exists():
            self.lock_file.unlink()
            return True
        return False

    def sync_memory(self, bot_name: str, memory_path: Path) -> Dict[str, Any]:
        """Merge a local MEMORY.md file with shared memory entries."""

        memory_path = Path(memory_path)
        self._ensure_shared_dir()
        with self._lock():
            state = self._read_state()
            memory_state = state.setdefault("memory", {})
            entries = memory_state.setdefault("entries", {})
            bots = memory_state.setdefault("bots", {})

            existing_text = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
            existing_entries = parse_memory_entries(existing_text)

            if memory_path.exists():
                source_timestamp = datetime.fromtimestamp(
                    memory_path.stat().st_mtime,
                    tz=timezone.utc,
                ).replace(microsecond=0).isoformat()
            else:
                source_timestamp = utc_now()

            for key, line in existing_entries.items():
                current = entries.get(key)
                if current is None or source_timestamp >= current.get("updated_at", ""):
                    entries[key] = {
                        "line": line,
                        "updated_at": source_timestamp,
                        "updated_by": bot_name,
                    }

            bots[bot_name] = {
                "memory_path": str(memory_path),
                "entry_count": len(entries),
                "last_synced_at": utc_now(),
            }
            merged_text = render_memory(entries)
            known_paths = {
                Path(bot_info["memory_path"])
                for bot_info in bots.values()
                if isinstance(bot_info, dict) and bot_info.get("memory_path")
            }
            known_paths.add(memory_path)
            for known_path in known_paths:
                current_text = known_path.read_text(encoding="utf-8") if known_path.exists() else ""
                if current_text != merged_text:
                    atomic_write_text(known_path, merged_text)
            self._write_state(state)

            return {
                "bot": bot_name,
                "memory_path": str(memory_path),
                "entry_count": len(entries),
                "updated": merged_text != existing_text,
            }

    def claim_task(self, task: str, bot_name: str, allow_takeover: bool = False) -> Dict[str, Any]:
        """Claim a task in shared state, preventing duplicate work."""

        task_key = normalize_task(task)
        now = utc_now()
        with self._lock():
            state = self._read_state()
            tasks = state.setdefault("tasks", {})
            existing = tasks.get(task_key)
            if (
                existing
                and existing.get("status") == "in_progress"
                and existing.get("owner") != bot_name
                and not allow_takeover
            ):
                return {
                    "claimed": False,
                    "task": task,
                    "owner": existing.get("owner"),
                    "status": existing.get("status"),
                }

            tasks[task_key] = {
                "task": task,
                "owner": bot_name,
                "status": "in_progress",
                "started_at": existing.get("started_at", now) if existing else now,
                "updated_at": now,
            }
            self._write_state(state)
            return {
                "claimed": True,
                "task": task,
                "owner": bot_name,
                "status": "in_progress",
            }

    def release_task(self, task: str, bot_name: str, force: bool = False) -> Dict[str, Any]:
        """Release a claimed task so another bot can pick it up."""

        task_key = normalize_task(task)
        now = utc_now()
        with self._lock():
            state = self._read_state()
            tasks = state.setdefault("tasks", {})
            existing = tasks.get(task_key)
            if not existing:
                return {"released": False, "reason": "not_found", "task": task}
            if existing.get("owner") != bot_name and not force:
                return {
                    "released": False,
                    "reason": "owned_by_other_bot",
                    "owner": existing.get("owner"),
                    "task": task,
                }
            existing["status"] = "released"
            existing["updated_at"] = now
            existing["released_by"] = bot_name
            self._write_state(state)
            return {
                "released": True,
                "task": task,
                "owner": existing.get("owner"),
                "status": "released",
            }

    def handoff_task(
        self,
        task: str,
        bot_name: str,
        target_bot: Optional[str] = None,
        release: bool = False,
        allow_takeover: bool = False,
    ) -> Dict[str, Any]:
        """Claim, transfer, or release a task depending on the requested action."""

        if release:
            return self.release_task(task, bot_name, force=allow_takeover)

        if target_bot and target_bot != bot_name:
            task_key = normalize_task(task)
            now = utc_now()
            with self._lock():
                state = self._read_state()
                tasks = state.setdefault("tasks", {})
                existing = tasks.get(task_key)
                if existing and existing.get("owner") != bot_name and not allow_takeover:
                    return {
                        "claimed": False,
                        "task": task,
                        "owner": existing.get("owner"),
                        "status": existing.get("status"),
                    }

                tasks[task_key] = {
                    "task": task,
                    "owner": target_bot,
                    "status": "in_progress",
                    "started_at": existing.get("started_at", now) if existing else now,
                    "updated_at": now,
                    "handoff_from": bot_name,
                }
                self._write_state(state)
                return {
                    "claimed": True,
                    "task": task,
                    "owner": target_bot,
                    "status": "in_progress",
                }

        return self.claim_task(task, bot_name, allow_takeover=allow_takeover)

    def write_status(
        self,
        bot_name: str,
        status: str,
        task: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Write the current bot status into the shared status file."""

        self._ensure_shared_dir()
        with self._lock():
            status_payload = read_json(self.status_file, default={"bots": {}})
            bots = status_payload.setdefault("bots", {})
            bots[bot_name] = {
                "status": status,
                "task": task,
                "details": details or {},
                "updated_at": utc_now(),
            }
            status_payload["updated_at"] = utc_now()
            atomic_write_json(self.status_file, status_payload)
            return bots[bot_name]


def json_dumps(data: Dict[str, Any]) -> str:
    """Render JSON for CLI output."""

    return json.dumps(data, indent=2, sort_keys=True)


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser."""

    parser = argparse.ArgumentParser(description="OpenClaw cross-bot sync coordinator")
    parser.add_argument(
        "--shared-dir",
        type=Path,
        default=None,
        help="Override the shared state directory (defaults to ~/.openclaw/shared).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="Sync a MEMORY.md file with shared state.")
    sync_parser.add_argument("--bot", required=True, help="Bot name, for example 'main' or 'tasks'.")
    sync_parser.add_argument(
        "--memory-path",
        type=Path,
        default=None,
        help="Path to the local MEMORY.md file. Defaults to ./MEMORY.md.",
    )

    handoff_parser = subparsers.add_parser("handoff", help="Claim, transfer, or release a task.")
    handoff_parser.add_argument("task", help="Task description to record in shared state.")
    handoff_parser.add_argument("--bot", required=True, help="Bot performing the action.")
    handoff_parser.add_argument("--to", dest="target_bot", help="Optional target bot for a handoff.")
    handoff_parser.add_argument(
        "--release",
        action="store_true",
        help="Release the task instead of claiming it.",
    )
    handoff_parser.add_argument(
        "--allow-takeover",
        action="store_true",
        help="Allow taking over an existing task claim.",
    )

    status_parser = subparsers.add_parser("status", help="Write bot status to the shared status file.")
    status_parser.add_argument("--bot", required=True, help="Bot writing the status.")
    status_parser.add_argument("--state", required=True, help="Short status string, such as 'idle'.")
    status_parser.add_argument("--task", help="Optional current task description.")
    status_parser.add_argument(
        "--details",
        default="{}",
        help="Optional JSON object with extra status metadata.",
    )

    subparsers.add_parser("unlock", help="Remove the shared lock file if it exists.")

    return parser


def run_cli(argv: Optional[Iterable[str]] = None) -> int:
    """Run the CLI and print JSON responses."""

    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    coordinator = CrossBotSyncCoordinator(shared_dir=args.shared_dir)

    if args.command == "sync":
        memory_path = args.memory_path or Path(os.environ.get("OPENCLAW_MEMORY_PATH", DEFAULT_MEMORY_FILE))
        result = coordinator.sync_memory(args.bot, memory_path)
        print(json_dumps(result))
        return 0

    if args.command == "handoff":
        result = coordinator.handoff_task(
            task=args.task,
            bot_name=args.bot,
            target_bot=args.target_bot,
            release=args.release,
            allow_takeover=args.allow_takeover,
        )
        print(json_dumps(result))
        return 0 if result.get("claimed", result.get("released", False)) else 1

    if args.command == "status":
        try:
            details = json.loads(args.details)
        except json.JSONDecodeError as exc:
            parser.error(f"--details must be valid JSON: {exc}")
        if not isinstance(details, dict):
            parser.error("--details must decode to a JSON object")
        result = coordinator.write_status(
            bot_name=args.bot,
            status=args.state,
            task=args.task,
            details=details,
        )
        print(json_dumps(result))
        return 0

    if args.command == "unlock":
        removed = coordinator.unlock()
        print(json_dumps({"unlocked": removed, "lock_file": str(coordinator.lock_file)}))
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


def main() -> None:
    """Entrypoint for ``python -m src.coordination.cross_bot_sync``."""

    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
