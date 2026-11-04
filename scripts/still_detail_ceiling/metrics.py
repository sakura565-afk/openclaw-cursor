"""Texture metrics measured on one fixed ROI and one common pixel scale."""

from __future__ import annotations

import cv2
import numpy as np
from scipy import ndimage

from scripts.skin_texture_metrics.metrics import (
    BLUR_SIGMA,
    PORE_BAND,
    is_pore_like,
    measure_patch,
)

PORE_BAND_PX = PORE_BAND
BLUR_SIGMA_PX = BLUR_SIGMA


def _gray(image: np.ndarray) -> np.ndarray:
    array = np.asarray(image)
    if array.ndim == 3:
        if array.shape[2] < 3:
            raise ValueError("expected three colour channels")
        array = cv2.cvtColor(array[:, :, :3], cv2.COLOR_RGB2GRAY)
    if array.ndim != 2 or min(array.shape) < 2:
        raise ValueError("expected a non-empty grayscale or RGB image")
    return array.astype(np.float32, copy=False)


def _ratio(after: float, before: float) -> float:
    return float(after / before) if before > 1e-12 else 0.0


def measure_texture(image: np.ndarray) -> dict[str, float]:
    """Measure pore-band energy and two distinct blur-survival ratios.

    ``pore_survival`` is FFT energy in the 3--12 px band after sigma=1.2
    Gaussian blur divided by the energy before blur. ``hf_survival`` is the
    same before/after ratio for mean absolute Laplacian HF. The latter is the
    erosion-style survival ratio; it is not called pore energy because edges,
    halos and noise all contribute to it.
    """
    gray = _gray(image)
    blurred = ndimage.gaussian_filter(gray, sigma=BLUR_SIGMA_PX)
    task1 = measure_patch(gray)
    pore = float(task1["pore"])
    pore_blurred = float(task1["pore_blur"])
    laplacian = float(task1["lap"])
    laplacian_blurred = float(np.abs(ndimage.laplace(blurred)).mean())
    pore_survival = float(task1["survival"])
    return {
        "pore_energy": pore,
        "pore_energy_after_blur": pore_blurred,
        "pore_survival": pore_survival,
        "surviving_pore_energy": pore * pore_survival,
        "pore_rms": float(task1["pore_rms"]),
        "structure_score": float(task1["structure_score"]),
        "task1_classification": str(task1["classification"]),
        "laplacian": laplacian,
        "laplacian_after_blur": laplacian_blurred,
        "hf_survival": _ratio(laplacian_blurred, laplacian),
        "pore_band_px_lo": PORE_BAND_PX[0],
        "pore_band_px_hi": PORE_BAND_PX[1],
        "blur_sigma_px": BLUR_SIGMA_PX,
    }


def pore_like_gate(before: np.ndarray, after: np.ndarray) -> tuple[bool, str]:
    """Run Task 1's rollback gate on the actual added component."""
    return is_pore_like(before, after)


def percent_delta(after: float, before: float) -> float | None:
    """Return a finite percentage, or null when the baseline has no energy."""
    if abs(before) <= 1e-12:
        return None
    return float((after / before - 1.0) * 100.0)


def comparison_metrics(candidate: dict, baseline: dict) -> dict[str, float | None]:
    """Compare a candidate with Lanczos at the same target geometry."""
    return {
        "pore_delta_pct_vs_lanczos": percent_delta(
            candidate["pore_energy"], baseline["pore_energy"]
        ),
        "surviving_pore_delta_pct_vs_lanczos": percent_delta(
            candidate["surviving_pore_energy"], baseline["surviving_pore_energy"]
        ),
        "pore_survival_delta_pp_vs_lanczos": float(
            (candidate["pore_survival"] - baseline["pore_survival"]) * 100.0
        ),
        "hf_survival_delta_pp_vs_lanczos": float(
            (candidate["hf_survival"] - baseline["hf_survival"]) * 100.0
        ),
        "laplacian_delta_pct_vs_lanczos": percent_delta(
            candidate["laplacian"], baseline["laplacian"]
        ),
    }


def oversharpening_suspected(row: dict) -> bool:
    """Flag extra edge energy only when blur survival moves the other way."""
    if row.get("status") != "ok":
        return False
    lap_delta = row.get("laplacian_delta_pct_vs_lanczos")
    survival_delta = row.get("pore_survival_delta_pp_vs_lanczos")
    return bool(
        lap_delta is not None
        and survival_delta is not None
        and lap_delta > 5.0
        and survival_delta < 0.0
    )
