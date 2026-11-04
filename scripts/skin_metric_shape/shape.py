"""Spatial shape and sparsity measurements for discrete pores versus grain.

The primary statistic is the number of signed, 8-connected high-pass components
per 10,000 pixels.  It measures spatial discreteness independently of FFT blur
survival.  Constants below are midpoints between the four constructive core
controls (flat plastic, coarse pores, white noise, and pores plus noise), not
from the four fine-pore failures that this statistic is meant to diagnose.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

HIGHPASS_SIGMA = 3.0
CORE_PORE_COMPONENT_DENSITY_MAX = 50.998263888888886
CORE_SAND_COMPONENT_DENSITY_MIN = 1099.4466145833333
SHAPE_MAX_COMPONENT_DENSITY = (
    CORE_PORE_COMPONENT_DENSITY_MAX + CORE_SAND_COMPONENT_DENSITY_MIN
) / 2.0
SHAPE_MIN_COMPONENT_DENSITY = CORE_PORE_COMPONENT_DENSITY_MAX / 2.0
SHAPE_MIN_PORE_RMS = 2.9056831236661


def _gray(image: np.ndarray) -> np.ndarray:
    array = np.asarray(image, dtype=np.float64)
    if array.ndim == 3 and array.shape[2] >= 3:
        array = array[..., :3].mean(axis=2)
    if array.ndim != 2 or not array.size:
        raise ValueError(f"expected a non-empty gray or RGB image, got {array.shape}")
    if not np.isfinite(array).all():
        raise ValueError("image must contain only finite values")
    return array


def highpass(image: np.ndarray) -> np.ndarray:
    """Remove shading at 3 px sigma while retaining pore-scale signed pits."""
    gray = _gray(image)
    return gray - ndimage.gaussian_filter(gray, HIGHPASS_SIGMA)


def _signed_components(detail: np.ndarray) -> tuple[list[int], list[int], float]:
    scale = max(float(detail.std()), 1e-9)
    threshold = max(1.0, scale)
    structure = np.ones((3, 3), dtype=np.uint8)
    dark_labels, dark_count = ndimage.label(detail < -threshold, structure=structure)
    bright_labels, bright_count = ndimage.label(detail > threshold, structure=structure)
    dark_sizes = [
        int(value)
        for value in ndimage.sum(np.ones(detail.shape), dark_labels, range(1, dark_count + 1))
    ] if dark_count else []
    bright_sizes = [
        int(value)
        for value in ndimage.sum(np.ones(detail.shape), bright_labels, range(1, bright_count + 1))
    ] if bright_count else []
    return dark_sizes, bright_sizes, scale


def measure_shape(image: np.ndarray) -> dict[str, float]:
    """Measure the primary feature and all five compared shape candidates.

    ``component_density_per_10k`` is the selected feature.  The other values
    are retained so candidate selection is reproducible rather than anecdotal.
    Equivalent component width is ``2*sqrt(area/pi)`` and is therefore a size
    statistic, not a claim that every component is circular.
    """
    gray = _gray(image)
    detail = highpass(gray)
    dark_sizes, bright_sizes, scale = _signed_components(detail)
    component_sizes = dark_sizes + bright_sizes
    component_count = len(component_sizes)
    component_area = int(sum(component_sizes))
    widths = [2.0 * np.sqrt(size / np.pi) for size in component_sizes]

    active_threshold = max(0.75, 0.35 * scale)
    active = np.abs(detail) > active_threshold
    maxima = (
        (detail == ndimage.maximum_filter(detail, size=3))
        & (detail > active_threshold)
    )
    minima = (
        (detail == ndimage.minimum_filter(detail, size=3))
        & (detail < -active_threshold)
    )
    peaks = maxima | minima
    nonpeak_active_area = int(np.count_nonzero(active & ~peaks))
    lap = np.abs(ndimage.laplace(detail))
    if np.any(peaks) and float(lap.std()) > 0.0:
        laplacian_peak_correlation = float(
            np.corrcoef(lap.ravel(), peaks.astype(np.float64).ravel())[0, 1]
        )
    else:
        laplacian_peak_correlation = 0.0

    density = component_count * 10_000.0 / gray.size
    sparsity_range = CORE_SAND_COMPONENT_DENSITY_MIN - CORE_PORE_COMPONENT_DENSITY_MAX
    sparsity = float(np.clip(
        (CORE_SAND_COMPONENT_DENSITY_MIN - density) / sparsity_range,
        0.0,
        1.0,
    ))
    return {
        "component_count": float(component_count),
        "component_density_per_10k": float(density),
        "component_area_fraction": component_area / gray.size,
        "median_component_width_px": float(np.median(widths)) if widths else 0.0,
        "p90_component_width_px": float(np.percentile(widths, 90)) if widths else 0.0,
        "sparsity_score": sparsity,
        "peak_area_fraction": float(np.count_nonzero(peaks) / max(np.count_nonzero(active), 1)),
        "peak_density_per_10k": float(np.count_nonzero(peaks) * 10_000.0 / gray.size),
        "pit_noise_area_ratio": component_area / max(nonpeak_active_area, 1),
        "laplacian_peak_correlation": laplacian_peak_correlation,
        "highpass_std": scale,
    }
