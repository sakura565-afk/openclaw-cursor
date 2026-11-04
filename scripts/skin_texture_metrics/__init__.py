"""Differentiate pore-scale skin structure from digital sand and plastic."""

from .metrics import (
    BLUR_SIGMA,
    DEFAULT_ROIS,
    MIN_ADDED_SURVIVAL,
    PORE_BAND,
    classify,
    is_pore_like,
    materials_root,
    measure_frames,
    measure_image,
    measure_patch,
)

__all__ = [
    "BLUR_SIGMA",
    "DEFAULT_ROIS",
    "MIN_ADDED_SURVIVAL",
    "PORE_BAND",
    "classify",
    "format_table",
    "is_pore_like",
    "materials_root",
    "measure_frames",
    "measure_image",
    "measure_patch",
]


def format_table(rows: list[dict]) -> str:
    """Imported lazily so the core API has no CLI dependency."""
    from .cli import format_table as implementation

    return implementation(rows)


from .cli import main  # noqa: E402
