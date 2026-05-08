"""Scan logs, classify errors, persist learnings, and optionally sync MEMORY.md."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from src.error_learning.log_signals import ERROR_SIGNAL_RE, iter_log_file_lines, normalize_error_line
from src.error_learning.memory_bridge import sync_observations_to_memory
from src.error_learning.store import ErrorLearningStore, ErrorObservation
from src.error_learning.taxonomy import ErrorCategory, classify_error_text


def _default_store_path(root: Path) -> Path:
    override = os.environ.get("OPENCLAW_ERROR_LEARNING_STORE")
    if override:
        return Path(override).expanduser()
    return root / "logs" / "error_learning_store.json"


def _default_log_roots() -> list[Path]:
    home = Path(os.environ.get("OPENCLAW_HOME") or Path.home() / ".openclaw").expanduser()
    return [home / "logs", home / "workspace" / "logs", Path.cwd() / "logs"]


class ErrorLearningEngine:
    """Capture recurring failures as structured observations."""

    def __init__(
        self,
        *,
        root_dir: Path | None = None,
        store_path: Path | None = None,
        log_roots: list[Path] | None = None,
    ) -> None:
        self.root_dir = Path(root_dir or Path.cwd())
        self.store_path = store_path or _default_store_path(self.root_dir)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_roots = log_roots if log_roots is not None else _default_log_roots()
        self._store = ErrorLearningStore(self.store_path)

    def ingest_line(self, line: str, *, source: str | None = None) -> ErrorObservation | None:
        if not ERROR_SIGNAL_RE.search(line):
            return None
        normalized = normalize_error_line(line)
        classification = classify_error_text(line)
        cat_value = classification.category.value
        if classification.category == ErrorCategory.UNKNOWN:
            fallback = classify_error_text(normalized)
            if fallback.category != ErrorCategory.UNKNOWN:
                cat_value = fallback.category.value
        return self._store.merge_observation(
            category=cat_value,
            normalized_text=normalized,
            source=source,
        )

    def scan_logs(self) -> list[ErrorObservation]:
        for log_path, line in iter_log_file_lines(self.log_roots):
            self.ingest_line(line, source=str(log_path))
        return self._store.list_observations()

    def ingest_lines(self, lines: Iterable[str], *, source: str | None = None) -> list[ErrorObservation]:
        recorded: list[ErrorObservation] = []
        for line in lines:
            obs = self.ingest_line(line, source=source)
            if obs:
                recorded.append(obs)
        return recorded

    def observations(self) -> list[ErrorObservation]:
        return self._store.list_observations()

    def sync_memory(self, memory_path: Path | None = None, *, max_entries: int = 40) -> Path:
        path = memory_path or Path(
            os.environ.get("OPENCLAW_MEMORY_PATH") or Path.cwd() / "MEMORY.md"
        ).expanduser()
        return sync_observations_to_memory(path, self.observations(), max_entries=max_entries)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for obs in self.observations():
            counts[obs.category] = counts.get(obs.category, 0) + obs.count
        return dict(sorted(counts.items()))
