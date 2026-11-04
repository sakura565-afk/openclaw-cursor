"""Pore-band structure metrics built on the repository's existing CPU probes."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
from PIL import Image

from scripts import compare_video_texture
from scripts.eval_judge.metrics import gaussian_blur, laplacian
from scripts.probe_pore_spectrum import band_energy, roi_slice
from scripts.skin_metric_shape import (
    SHAPE_MAX_COMPONENT_DENSITY,
    SHAPE_MIN_COMPONENT_DENSITY,
    SHAPE_MIN_PORE_RMS,
    measure_shape,
    measure_shape_sequence,
)

PORE_BAND = (3.0, 12.0)
SPEC_BAND = (25.0, 1e9)
BLUR_SIGMA = 1.2
DEFAULT_ROIS = dict(compare_video_texture.DEFAULT_ROIS)

# These decision thresholds are calibrated on the eye-labelled clips documented
# in README.md. They are not universal biological constants. RMS normalisation
# removes FFT size and mean brightness; the original energy remains available.
PLASTIC_MAX_PORE_RMS = 4.90
SAND_MAX_SURVIVAL = 0.60
MIN_ADDED_SURVIVAL = 0.45
MIN_ENERGY_GROWTH = 0.02


def materials_root() -> Path:
    """Locate real inputs without embedding a workstation path in source."""
    for variable in ("HERMES_NATIVE_INPUTS", "HERMES_SCRATCH_DIR"):
        value = os.environ.get(variable, "").strip()
        if value:
            return Path(value)
    return Path.home() / "AppData" / "Local" / "hermes" / "cache" / "scratch"


def _as_rgb(image: np.ndarray | str | os.PathLike[str]) -> np.ndarray:
    if isinstance(image, (str, os.PathLike)):
        with Image.open(image) as opened:
            return np.asarray(opened.convert("RGB"), dtype=np.float64)
    array = np.asarray(image)
    if array.ndim == 2:
        return np.asarray(array, dtype=np.float64)
    if array.ndim == 3 and array.shape[2] >= 3:
        return np.asarray(array[..., :3], dtype=np.float64)
    raise ValueError(f"expected a gray or RGB image, got shape {array.shape}")


def _gray(image: np.ndarray | str | os.PathLike[str]) -> np.ndarray:
    array = _as_rgb(image)
    if array.ndim == 3:
        array = array.mean(axis=2)
    if not array.size or not np.isfinite(array).all():
        raise ValueError("image must be non-empty and finite")
    return np.asarray(array, dtype=np.float64)


def _unit_interval(value: float, low: float, high: float) -> float:
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


def legacy_classify(metrics: Mapping[str, float]) -> str:
    """The pre-shape classifier, retained for audits and backwards callers."""
    if float(metrics["pore_rms"]) < PLASTIC_MAX_PORE_RMS:
        return "plastic"
    if float(metrics["survival"]) < SAND_MAX_SURVIVAL:
        return "digital_sand"
    return "pores"


def classify(metrics: Mapping[str, float]) -> str:
    """Classify energy, blur survival, and discrete-component sparsity.

    Mappings without shape fields follow the legacy decision exactly.  When
    shape is present, a measured minimum activity and component density prevent
    flat plates or a few shading blobs from being promoted to pores.
    """
    density = metrics.get("shape_component_density_per_10k")
    if density is not None:
        pore_rms = float(metrics["pore_rms"])
        if (
            pore_rms >= SHAPE_MIN_PORE_RMS
            and SHAPE_MIN_COMPONENT_DENSITY <= float(density) <= SHAPE_MAX_COMPONENT_DENSITY
        ):
            return "pores"
    return legacy_classify(metrics)


def measure_patch(gray: np.ndarray) -> dict[str, float | str]:
    """Measure one gray ROI using the canonical FFT band and matched blur.

    ``pore_rms = sqrt(FFT energy) / pixel_count`` compensates the unnormalised
    FFT used by ``probe_pore_spectrum.py``. The structural score rewards pore
    band activity only when that activity survives the same sigma=1.2 blur.
    """
    array = _gray(gray)
    height, width = array.shape
    pixels = height * width
    pore = float(band_energy(array, *PORE_BAND))
    spec = float(band_energy(array, *SPEC_BAND))
    blurred = gaussian_blur(array, BLUR_SIGMA)
    pore_blur = float(band_energy(blurred, *PORE_BAND))
    survival = pore_blur / max(pore, 1e-9)
    pore_rms = math.sqrt(max(pore, 0.0)) / pixels
    activity = _unit_interval(pore_rms, PLASTIC_MAX_PORE_RMS, 6.20)
    coherent = _unit_interval(survival, 0.45, 0.80)
    result: dict[str, float | str] = {
        "pore": pore,
        "pore_blur": pore_blur,
        "spec": spec,
        "pore_ratio": pore / max(spec, 1e-9),
        "pore_rms": pore_rms,
        "survival": survival,
        "structure_score": activity * coherent,
        "lap": float(np.abs(laplacian(array)).mean()),
    }
    for name, value in measure_shape(array).items():
        result[f"shape_{name}"] = value
    result["classification"] = classify(result)
    return result


def _resize_gray(gray: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    height, width = size
    if gray.shape == (height, width):
        return gray
    image = Image.fromarray(np.clip(gray, 0.0, 255.0).astype(np.uint8), mode="L")
    return np.asarray(image.resize((width, height), Image.LANCZOS), dtype=np.float64)


def _aggregate(rows: Sequence[Mapping[str, float | str]]) -> dict[str, float | str]:
    if not rows:
        raise ValueError("no image regions to measure")
    keys = ("pore", "pore_blur", "spec", "pore_ratio", "pore_rms", "structure_score", "lap")
    shape_keys = tuple(key for key in rows[0] if key.startswith("shape_"))
    result: dict[str, float | str] = {
        key: float(np.mean([float(row[key]) for row in rows])) for key in keys + shape_keys
    }
    # Aggregate energy before dividing; averaging per-ROI ratios would weight a
    # tiny, nearly empty crop as heavily as a large skin region.
    result["survival"] = float(result["pore_blur"]) / max(float(result["pore"]), 1e-9)
    result["classification"] = classify(result)
    result["regions"] = len(rows)
    return result


def measure_image(
    image: np.ndarray | str | os.PathLike[str],
    *,
    rois: Mapping[str, Sequence[float]] | None = None,
    canonical: Mapping[str, tuple[int, int]] | None = None,
) -> dict:
    """Measure an image; by default the whole frame is one ROI."""
    gray = _gray(image)
    selected = {"full": (0.0, 0.0, 1.0, 1.0)} if rois is None else rois
    regions: dict[str, dict] = {}
    rows = []
    for name, roi in selected.items():
        y0, y1, x0, x1 = roi_slice(gray.shape, roi)
        crop = gray[y0:y1, x0:x1]
        if not crop.size:
            raise ValueError(f"empty ROI {name!r}")
        if canonical and name in canonical:
            crop = _resize_gray(crop, canonical[name])
        row = measure_patch(crop)
        regions[name] = row
        rows.append(row)
    result = _aggregate(rows)
    result["per_roi"] = regions
    return result


def measure_frames(
    paths: Sequence[str | os.PathLike[str]],
    *,
    rois: Mapping[str, Sequence[float]] | None = None,
    canonical: Mapping[str, tuple[int, int]] | None = None,
) -> dict:
    """Measure decoded frames, using the established skin ROIs by default."""
    if not paths:
        raise ValueError("no frames to measure")
    selected = DEFAULT_ROIS if rois is None else rois
    per_roi: dict[str, list[Mapping[str, float | str]]] = {name: [] for name in selected}
    crops_by_roi: dict[str, list[np.ndarray]] = {name: [] for name in selected}
    for path in paths:
        gray = _gray(path)
        for name, roi in selected.items():
            y0, y1, x0, x1 = roi_slice(gray.shape, roi)
            crop = gray[y0:y1, x0:x1]
            if not crop.size:
                raise ValueError(f"empty ROI {name!r} in {path}")
            if canonical and name in canonical:
                crop = _resize_gray(crop, canonical[name])
            crops_by_roi[name].append(crop)
            per_roi[name].append(measure_patch(crop))
    regions = {name: _aggregate(rows) for name, rows in per_roi.items()}
    temporal = {name: measure_shape_sequence(crops) for name, crops in crops_by_roi.items()}
    for name, region in regions.items():
        region["temporal_shape"] = temporal[name]
    all_rows = [row for rows in per_roi.values() for row in rows]
    result = _aggregate(all_rows)
    correlations = [
        value
        for row in temporal.values()
        for value in row["neighbor_highpass_correlations"]
    ]
    result["temporal_shape"] = {
        "neighbor_comparisons": len(correlations),
        "neighbor_highpass_correlations": correlations,
        "mean_highpass_correlation": float(np.mean(correlations)) if correlations else 1.0,
        "temporal_component_stability": float(np.mean([
            row["temporal_component_stability"] for row in temporal.values()
        ])),
    }
    result["per_roi"] = regions
    result["frames"] = len(paths)
    return result


def is_pore_like(
    before: np.ndarray | str | os.PathLike[str],
    after: np.ndarray | str | os.PathLike[str],
    *,
    rois: Mapping[str, Sequence[float]] | None = None,
    min_added_survival: float = MIN_ADDED_SURVIVAL,
    min_energy_growth: float = MIN_ENERGY_GROWTH,
) -> tuple[bool, str]:
    """Automatic rollback gate for a before/after pair.

    The added component, not just the finished image, must survive the matched
    blur. This distinction rejects BC output: its base skin keeps a high raw
    survival while the actual BC delta has noise-like survival.
    """
    try:
        before_gray = _gray(before)
        after_gray = _gray(after)
    except (OSError, ValueError) as error:
        return False, f"invalid input: {error}"
    if before_gray.shape != after_gray.shape:
        return False, f"shape mismatch: {before_gray.shape} != {after_gray.shape}"
    before_metrics = measure_image(before_gray, rois=rois)
    after_metrics = measure_image(after_gray, rois=rois)
    added_metrics = measure_image(after_gray - before_gray, rois=rois)
    growth = (float(after_metrics["pore"]) - float(before_metrics["pore"])) / max(
        float(before_metrics["pore"]), 1e-9
    )
    if growth <= min_energy_growth:
        return False, f"pore energy growth {growth:.3f} <= {min_energy_growth:.3f}"
    added_survival = float(added_metrics["survival"])
    if added_survival < min_added_survival:
        return False, (
            f"added-band survival {added_survival:.3f} < {min_added_survival:.3f}; "
            "the added component is noise-like"
        )
    if after_metrics["classification"] != "pores":
        return False, (
            f"finished image is {after_metrics['classification']}, not pores "
            f"(survival={float(after_metrics['survival']):.3f}, "
            f"pore_rms={float(after_metrics['pore_rms']):.3f})"
        )
    return True, (
        f"pore energy +{growth * 100:.1f}%, added-band survival "
        f"{added_survival:.3f}, structure={float(after_metrics['structure_score']):.3f}"
    )
