"""Aspect-ratio preserving output geometry for still upscalers."""

from __future__ import annotations


def scaling_plan(
    source_width: int,
    source_height: int,
    target_width: int = 1072,
    alignment: int = 32,
    aspect_tolerance_pct: float = 1.0,
) -> dict:
    """Derive height from source AR, then round only to the alignment grid.

    Exact source AR and arbitrary width/height alignment are usually
    incompatible. The report therefore exposes the rounding error instead of
    silently stretching the image. An error above the declared tolerance is a
    violation; it is never hidden by recomputing the source aspect ratio.
    """
    values = (source_width, source_height, target_width, alignment)
    if any(isinstance(value, bool) or int(value) != value for value in values):
        raise ValueError("dimensions and alignment must be integers")
    source_width, source_height, target_width, alignment = map(int, values)
    if min(source_width, source_height, target_width, alignment) <= 0:
        raise ValueError("dimensions and alignment must be positive")
    if aspect_tolerance_pct < 0.0:
        raise ValueError("aspect tolerance must be non-negative")
    requested_width = target_width
    aligned_width = max(alignment, round(target_width / alignment) * alignment)
    target_width = aligned_width

    raw_height = source_height * target_width / source_width
    target_height = max(alignment, round(raw_height / alignment) * alignment)
    signed_height_error = (target_height / raw_height - 1.0) * 100.0
    aspect_error = abs(signed_height_error)
    violations = []
    if aspect_error > aspect_tolerance_pct:
        violations.append("aspect_ratio_error_exceeds_tolerance")
    return {
        "source_width": source_width,
        "source_height": source_height,
        "requested_width": requested_width,
        "target_width": target_width,
        "target_height": target_height,
        "width_adjustment_px": target_width - requested_width,
        "width_alignment_adjusted": target_width != requested_width,
        "raw_target_height": raw_height,
        "alignment": alignment,
        "aspect_error_pct": aspect_error,
        "signed_height_error_pct": signed_height_error,
        "aspect_tolerance_pct": float(aspect_tolerance_pct),
        "violations": violations,
    }
