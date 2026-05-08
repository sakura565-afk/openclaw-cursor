"""Error learning: categorize log signals, deduplicate, persist, sync to MEMORY.md."""

from src.error_learning.engine import ErrorLearningEngine
from src.error_learning.log_signals import ERROR_SIGNAL_RE, normalize_error_line
from src.error_learning.store import ErrorObservation, ErrorLearningStore, compute_fingerprint
from src.error_learning.taxonomy import Classification, ErrorCategory, classify_error_text

__all__ = [
    "Classification",
    "ErrorCategory",
    "ErrorLearningEngine",
    "ErrorLearningStore",
    "ErrorObservation",
    "ERROR_SIGNAL_RE",
    "classify_error_text",
    "compute_fingerprint",
    "normalize_error_line",
]
