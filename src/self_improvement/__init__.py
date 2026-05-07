"""Self-improvement utilities for OpenClaw."""

from .auto_engine import AutoImprovementEngine
from .error_learning import ErrorContext, ErrorEvent, ErrorLearningEngine

__all__ = [
    "AutoImprovementEngine",
    "ErrorContext",
    "ErrorEvent",
    "ErrorLearningEngine",
]
