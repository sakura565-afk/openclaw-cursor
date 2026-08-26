"""Persistent state management for pipeline resume/recovery."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import portalocker

StateDict = dict[str, Any]


class ItemStatus(str, Enum):
    """Status of a single image x format render item."""

    PENDING = "pending"
    RENDERING = "rendering"
    DONE = "done"
    FAILED = "failed"


def _utc_now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class StateManager:
    """JSON state file with atomic writes and cross-platform locking."""

    def __init__(self, state_file: Path, project: str) -> None:
        """Initialize state manager.

        Args:
            state_file: Path to the JSON state file.
            project: Project name stored in state metadata.
        """
        self.state_file = state_file
        self.project = project

    def _lock_path(self) -> Path:
        """Return dedicated lock file path (separate from state data)."""
        return self.state_file.with_suffix(self.state_file.suffix + ".lock")

    def _read_state_unlocked(self) -> StateDict:
        """Read state from disk without acquiring a lock."""
        if not self.state_file.exists():
            return self._fresh_state()
        content = self.state_file.read_text(encoding="utf-8")
        if not content.strip():
            return self._fresh_state()
        return json.loads(content)

    def _write_state_atomic(self, state: StateDict) -> None:
        """Write state atomically via temp file + rename."""
        state["updated_at"] = _utc_now_iso()
        tmp_path = self.state_file.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp_path.replace(self.state_file)

    def _mutate_state(self, mutator: Callable[[StateDict], None]) -> StateDict:
        """Load, mutate, and save state under a single file lock."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path()
        lock_path.touch(exist_ok=True)

        with portalocker.Lock(lock_path, "r+", timeout=10):
            state = self._read_state_unlocked()
            mutator(state)
            self._write_state_atomic(state)
            return state

    def load(self) -> StateDict:
        """Load state from disk or return a fresh state dict.

        Returns:
            State dictionary.
        """
        lock_path = self._lock_path()
        if not self.state_file.exists() and not lock_path.exists():
            return self._fresh_state()

        lock_path.touch(exist_ok=True)
        with portalocker.Lock(lock_path, "r", timeout=10):
            return self._read_state_unlocked()

    def save(self, state: StateDict) -> None:
        """Atomically save state to disk.

        Args:
            state: State dictionary to persist.
        """
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path()
        lock_path.touch(exist_ok=True)

        with portalocker.Lock(lock_path, "r+", timeout=10):
            self._write_state_atomic(state)

    def _fresh_state(self) -> StateDict:
        """Create a new empty state dict."""
        now = _utc_now_iso()
        return {
            "project": self.project,
            "started_at": now,
            "updated_at": now,
            "items": {},
        }

    def _ensure_item(self, state: StateDict, image: str, format_key: str) -> dict[str, Any]:
        """Ensure nested item entry exists and return it."""
        items = state.setdefault("items", {})
        image_items = items.setdefault(image, {})
        if format_key not in image_items:
            image_items[format_key] = {"status": ItemStatus.PENDING.value, "attempts": 0}
        return image_items[format_key]

    def update_item(self, image: str, format_key: str, **fields: Any) -> StateDict:
        """Update a single item and persist state.

        Args:
            image: Source image filename.
            format_key: Output format key (e.g. "1:1").
            **fields: Fields to update on the item record.

        Returns:
            Updated state dict.
        """
        def mutator(state: StateDict) -> None:
            item = self._ensure_item(state, image, format_key)
            item.update(fields)

        return self._mutate_state(mutator)

    def get_resumable_items(self) -> list[tuple[str, str]]:
        """Return (image, format) pairs that are pending or failed.

        Returns:
            List of resumable item tuples.
        """
        state = self.load()
        result: list[tuple[str, str]] = []
        for image, formats in state.get("items", {}).items():
            for fmt, item in formats.items():
                status = item.get("status", ItemStatus.PENDING.value)
                if status in (ItemStatus.PENDING.value, ItemStatus.FAILED.value):
                    result.append((image, fmt))
        return result

    def get_failed_items(self) -> list[tuple[str, str, str]]:
        """Return failed items with reasons.

        Returns:
            List of (image, format, reason) tuples.
        """
        state = self.load()
        result: list[tuple[str, str, str]] = []
        for image, formats in state.get("items", {}).items():
            for fmt, item in formats.items():
                if item.get("status") == ItemStatus.FAILED.value:
                    result.append((image, fmt, item.get("reason", "unknown")))
        return result

    def mark_done(
        self,
        image: str,
        format_key: str,
        output_path: str,
        **metrics: Any,
    ) -> StateDict:
        """Mark an item as successfully completed.

        Args:
            image: Source image filename.
            format_key: Output format key.
            output_path: Path to the final output video.
            **metrics: Additional metrics (duration_sec, size_mb, ar_drift, etc.).

        Returns:
            Updated state dict.
        """
        fields: dict[str, Any] = {
            "status": ItemStatus.DONE.value,
            "output_path": output_path,
            **metrics,
        }
        return self.update_item(image, format_key, **fields)

    def mark_failed(self, image: str, format_key: str, reason: str) -> StateDict:
        """Mark an item as failed with a reason.

        Args:
            image: Source image filename.
            format_key: Output format key.
            reason: Failure reason string.

        Returns:
            Updated state dict.
        """
        def mutator(state: StateDict) -> None:
            item = self._ensure_item(state, image, format_key)
            item["status"] = ItemStatus.FAILED.value
            item["reason"] = reason
            item["attempts"] = item.get("attempts", 0) + 1

        return self._mutate_state(mutator)

    def get_item_status(self, image: str, format_key: str) -> str | None:
        """Get the status of a specific item.

        Args:
            image: Source image filename.
            format_key: Output format key.

        Returns:
            Status string or None if item does not exist.
        """
        state = self.load()
        item = state.get("items", {}).get(image, {}).get(format_key)
        if item is None:
            return None
        return item.get("status")
