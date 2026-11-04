"""Neighbouring-frame evidence for the spatial shape statistic."""

from __future__ import annotations

from typing import Sequence

import numpy as np

from .shape import highpass, measure_shape


def _correlation(before: np.ndarray, after: np.ndarray) -> float:
    a = np.asarray(before, dtype=np.float64).ravel()
    b = np.asarray(after, dtype=np.float64).ravel()
    if a.shape != b.shape:
        raise ValueError(f"frame shape mismatch: {before.shape} != {after.shape}")
    a_std = float(a.std())
    b_std = float(b.std())
    if a_std == 0.0 or b_std == 0.0:
        return 1.0 if np.array_equal(a, b) else 0.0
    return float(np.corrcoef(a, b)[0, 1])


def measure_shape_sequence(images: Sequence[np.ndarray]) -> dict:
    """Measure every frame and compare adjacent high-pass pixel fields.

    Correlation is deliberately reported separately from component-count
    stability.  It is unregistered evidence: camera or skin motion can lower it
    even for real pores, so it is not used as a standalone rejection gate.
    """
    if not images:
        raise ValueError("no frames to measure")
    per_frame = [measure_shape(image) for image in images]
    details = [highpass(image) for image in images]
    correlations = [
        _correlation(before, after)
        for before, after in zip(details, details[1:])
    ]
    densities = [row["component_density_per_10k"] for row in per_frame]
    relative_changes = [
        abs(after - before) / max((after + before) / 2.0, 1e-9)
        for before, after in zip(densities, densities[1:])
    ]
    stability = 1.0 - float(np.mean(relative_changes)) if relative_changes else 1.0
    return {
        "frames": len(images),
        "per_frame": per_frame,
        "component_density_per_10k": float(np.mean(densities)),
        "median_component_width_px": float(np.mean([
            row["median_component_width_px"] for row in per_frame
        ])),
        "sparsity_score": float(np.mean([
            row["sparsity_score"] for row in per_frame
        ])),
        "neighbor_highpass_correlations": correlations,
        "neighbor_comparisons": len(correlations),
        "mean_highpass_correlation": float(np.mean(correlations)) if correlations else 1.0,
        "temporal_component_stability": float(np.clip(stability, 0.0, 1.0)),
    }
