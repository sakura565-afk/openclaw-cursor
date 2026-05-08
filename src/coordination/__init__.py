"""Coordination utilities for OpenClaw bots."""

from .cross_bot_sync import CrossBotSyncCoordinator, FileLock, LockTimeoutError
from .error_learning import (
    LearningRecord,
    ParsedSignal,
    categorize_error,
    default_jsonl_path,
    extract_error_signals,
    merge_lesson_into_memory,
    process_signals,
    register_learning,
)

__all__ = [
    "CrossBotSyncCoordinator",
    "FileLock",
    "LearningRecord",
    "LockTimeoutError",
    "ParsedSignal",
    "categorize_error",
    "default_jsonl_path",
    "extract_error_signals",
    "merge_lesson_into_memory",
    "process_signals",
    "register_learning",
]
