"""Independent, CPU-only ground truth for the skin-texture metric."""

from .synthetic import SyntheticCase, build_cases, generate_dataset
from .blind import build_blind_set
from .calibration import build_calibration_report, write_calibration_report

__all__ = [
    "SyntheticCase",
    "build_cases",
    "generate_dataset",
    "build_blind_set",
    "build_calibration_report",
    "write_calibration_report",
]
