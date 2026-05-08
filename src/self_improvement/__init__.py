"""Self-improvement utilities for OpenClaw."""

from src.self_improvement.auto_engine import AutoImprovementEngine, CheckResult, ImprovementAction
from src.self_improvement.error_learning import (
    AggregatedPattern,
    ErrorCategory,
    ErrorLearningSystem,
    categorize_error,
    normalize_error_text,
    signature_for,
)

__all__ = [
    "AggregatedPattern",
    "AutoImprovementEngine",
    "CheckResult",
    "ErrorCategory",
    "ErrorLearningSystem",
    "ImprovementAction",
    "categorize_error",
    "normalize_error_text",
    "signature_for",
]
