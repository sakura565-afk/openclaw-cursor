"""Long-chain texture gate built on the existing skin texture metrics."""

from .gate import (
    DEFAULT_MAX_STRUCTURE_DROP,
    DEFAULT_MAX_SURVIVAL_DROP,
    MIN_STRUCTURE,
    GateConfig,
    TextureGateResult,
    evaluate_metrics,
    format_chunk_report,
    make_chunk_report,
    measure_added_texture,
)

__all__ = [
    "DEFAULT_MAX_STRUCTURE_DROP",
    "DEFAULT_MAX_SURVIVAL_DROP",
    "MIN_STRUCTURE",
    "GateConfig",
    "TextureGateResult",
    "evaluate_metrics",
    "format_chunk_report",
    "make_chunk_report",
    "measure_added_texture",
]
