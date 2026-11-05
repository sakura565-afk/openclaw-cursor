"""Numerical still QC: texture detail minus fabric coverage.

This is deliberately not an anatomy classifier. It accepts only a textured region
whose pixels do not look like a uniform fabric panel bounded by coherent edges.
The score is useful for iteration and regression, not a claim of medical accuracy.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import cv2
import numpy as np


def _unit(value: float) -> float:
    return float(np.clip(value, 0.0, 1.0))


@dataclass(frozen=True)
class AnatomyMetrics:
    texture_energy: float
    texture_score: float
    uniform_fraction: float
    non_skin_uniform_fraction: float
    skin_fraction: float
    edge_energy: float
    structural_edge_energy: float
    cloth_score: float
    uncovered_score: float
    visibility: float
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _long_edge_score(edges: np.ndarray, mask: np.ndarray) -> float:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return 0.0
    span = max(8, min(xs.max() - xs.min() + 1, ys.max() - ys.min() + 1))
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 360.0,
        threshold=max(6, round(span * 0.08)),
        minLineLength=max(8, round(span * 0.18)),
        maxLineGap=max(2, round(span * 0.06)),
    )
    if lines is None:
        return 0.0
    longest = max(
        float(np.hypot(line[2] - line[0], line[3] - line[1]))
        for line in np.asarray(lines).reshape(-1, 4)
    )
    return _unit(longest / (span * 0.65))


def validate_anatomy(
    image_rgb: np.ndarray,
    mask: np.ndarray,
    *,
    pass_threshold: float = 0.45,
) -> AnatomyMetrics:
    """Measure anatomy visibility in ``0..1`` inside a pose-derived mask.

    Texture is the 75th percentile of high-pass luminance. Fabric is the stronger
    of (a) a uniform non-skin panel and (b) a uniform region with long structural
    edges. The second cue catches skin-coloured underwear; the texture requirement
    prevents a bare but perfectly smooth skin patch from being accepted.
    """
    if image_rgb.ndim != 3 or image_rgb.shape[2] != 3:
        raise ValueError("expected an HxWx3 RGB image")
    if mask.shape != image_rgb.shape[:2]:
        raise ValueError("mask and image dimensions differ")
    selected = mask > 127
    if np.count_nonzero(selected) < 64:
        raise ValueError("pelvis mask has fewer than 64 active pixels")
    rgb = np.ascontiguousarray(image_rgb, dtype=np.uint8)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    cv2.setRNGSeed(0)
    high_pass = gray - cv2.GaussianBlur(gray, (0, 0), 1.15)
    texture_energy = float(np.percentile(np.abs(high_pass[selected]), 75))
    texture_score = _unit((texture_energy - 0.55) / 1.25)

    gray_squared = gray * gray
    mean = cv2.boxFilter(gray, -1, (9, 9), normalize=True)
    mean_squared = cv2.boxFilter(gray_squared, -1, (9, 9), normalize=True)
    local_std = np.sqrt(np.maximum(mean_squared - mean * mean, 0.0))
    uniform = local_std < 2.25
    uniform_fraction = float(np.mean(uniform[selected]))

    ycrcb = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    y_channel = ycrcb[:, :, 0]
    cr = ycrcb[:, :, 1]
    cb = ycrcb[:, :, 2]
    skin = (y_channel > 35) & (cr >= 133) & (cr <= 183) & (cb >= 72) & (cb <= 136)
    non_skin_uniform_fraction = float(np.mean((uniform & ~skin)[selected]))
    # Satin, lace and grey censor blobs all carry high-frequency detail, so a
    # texture score alone cannot tell anatomy from fabric. The exposed region
    # has to be skin-coloured as well.
    skin_fraction = float(np.mean(skin[selected]))

    edges = cv2.Canny(gray.astype(np.uint8), 35, 105)
    edges[~selected] = 0
    edge_energy = float(np.mean(edges[selected] > 0))
    structural_edge_energy = _long_edge_score(edges, selected.astype(np.uint8))
    panel_score = non_skin_uniform_fraction * (0.70 + 0.30 * structural_edge_energy)
    edged_fabric_score = structural_edge_energy * (0.55 + 0.45 * uniform_fraction)
    cloth_score = _unit(max(panel_score, edged_fabric_score))
    uncovered_score = 1.0 - cloth_score
    # Texture alone is fooled by satin and lace, and skin tone alone is fooled
    # by warm fabric. Both have to hold for the region to read as exposed skin.
    visibility = _unit(texture_score * uncovered_score * min(1.0, skin_fraction / 0.55))
    passed = bool(
        visibility >= pass_threshold
        and texture_score >= 0.30
        and cloth_score <= 0.50
        and skin_fraction >= 0.55
    )
    return AnatomyMetrics(
        texture_energy=round(texture_energy, 6),
        texture_score=round(texture_score, 6),
        uniform_fraction=round(uniform_fraction, 6),
        non_skin_uniform_fraction=round(non_skin_uniform_fraction, 6),
        skin_fraction=round(skin_fraction, 6),
        edge_energy=round(edge_energy, 6),
        structural_edge_energy=round(structural_edge_energy, 6),
        cloth_score=round(cloth_score, 6),
        uncovered_score=round(uncovered_score, 6),
        visibility=round(visibility, 6),
        passed=passed,
    )
