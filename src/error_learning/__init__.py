"""Structured error learning: categorize, persist, sync to MEMORY.md."""

from src.error_learning.engine import (
    ErrorLearningEngine,
    ErrorLearningRecord,
    classify_error_line,
    default_learnings_path,
    default_log_roots,
    default_memory_path,
    iter_log_lines,
    memory_line,
    normalize_for_fingerprint,
    stable_fingerprint_id,
)

__all__ = [
    "ErrorLearningEngine",
    "ErrorLearningRecord",
    "classify_error_line",
    "default_learnings_path",
    "default_log_roots",
    "default_memory_path",
    "iter_log_lines",
    "memory_line",
    "normalize_for_fingerprint",
    "stable_fingerprint_id",
]
