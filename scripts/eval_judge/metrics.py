"""Objective metrics for the blind judge.

Where the numbers come from
---------------------------
* The pore band (spatial periods of 3-12 px) is the canonical implementation
  in ``scripts/probe_pore_spectrum.py`` and is imported, not re-derived: a
  second implementation would silently produce a different scale and every
  comparison would be meaningless.
* The temporal metrics (RGB delta, HF delta, face drift) follow
  ``scripts/compare_video_texture.py``: the RGB delta is the mean absolute
  difference between consecutive frames, the HF delta is the mean absolute
  difference of the Laplacians of consecutive frames, and the face drift is
  the same measure inside a normalised face ROI, against the first frame.
  When scipy is installed the module is imported and used directly, so the
  numbers are bit-for-bit the ones the older tooling prints. scipy is NOT in
  ``requirements.txt`` (CI installs wheels only), so a numpy fallback with the
  same formulas is used when it is missing; ``tests/test_eval_judge.py``
  asserts the two agree.

The one thing this module adds on top of the older tooling is the answer to
"is that high-frequency energy a pore or is it noise?". A real pore survives
a gaussian blur; isotropic pixel noise does not. So every pore measurement
comes with its ``survival`` ratio, and the report refuses to call a candidate
"more textured" when the energy comes with a low survival.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from PIL import Image

# Make ``scripts.probe_pore_spectrum`` importable no matter where the judge
# is started from. The repository root is two levels above this file.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.probe_pore_spectrum import band_energy  # type: ignore  # noqa: E402

try:  # pragma: no cover - depends on the host
    from scipy import ndimage as _ndimage  # type: ignore
except Exception:  # pragma: no cover - scipy is not a CI dependency
    _ndimage = None

# Spatial periods of a pore, in pixels. Same numbers as the older probe.
PORE_BAND = (3.0, 12.0)
# Spatial periods of a specular highlight, in pixels.
SPEC_BAND = (25.0, 1e9)
# A pore keeps most of its energy through a blur of this sigma.
BLUR_SIGMA = 1.2

# The face ROI used by scripts/compare_video_texture.py, in normalised
# (x0, y0, x1, y1). Overridable from the CLI for other framings.
DEFAULT_FACE_ROI = (0.327, 0.187, 0.568, 0.354)


# --------------------------------------------------------------------------
# small image helpers (numpy only, so the module works without scipy)
# --------------------------------------------------------------------------
def laplacian(gray: np.ndarray) -> np.ndarray:
    """4-neighbour Laplacian, matching scipy.ndimage.laplace."""
    if _ndimage is not None:
        return np.asarray(_ndimage.laplace(gray))
    padded = np.pad(gray, 1, mode="reflect")
    up = padded[0:-2, 1:-1]
    down = padded[2:, 1:-1]
    left = padded[1:-1, 0:-2]
    right = padded[1:-1, 2:]
    centre = padded[1:-1, 1:-1]
    return (up + down + left + right - 4.0 * centre)


def gaussian_blur(gray: np.ndarray, sigma: float = BLUR_SIGMA) -> np.ndarray:
    """Separable gaussian blur, matching scipy.ndimage.gaussian_filter.

    The fallback keeps the input shape. A separable blur is two 1-D
    convolutions: rows first, then columns, each with the SAME reflect
    padding on the axis being convolved. Padding both axes at once and then
    transposing changes the geometry, and a changed geometry silently
    invalidates the pore spectrum instead of reproducing it.
    """
    if _ndimage is not None:
        return np.asarray(_ndimage.gaussian_filter(gray, sigma=sigma))
    if sigma <= 0:
        return np.asarray(gray, dtype=np.float64)
    radius = max(1, int(4.0 * sigma + 0.5))
    offsets = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-(offsets ** 2) / (2.0 * sigma * sigma))
    kernel /= kernel.sum()

    height, width = gray.shape[:2]
    # Horizontal pass: pad the width only, convolve along the last axis.
    # scipy's "reflect" mode is numpy's "symmetric" (the sample on the edge
    # is mirrored), which is what makes the fallback agree with scipy to
    # machine precision rather than merely to the right order of magnitude.
    padded = np.pad(gray, ((0, 0), (radius, radius)), mode="symmetric")
    horizontal = np.zeros((height, width), dtype=np.float64)
    for index, weight in enumerate(kernel):
        horizontal += weight * padded[:, index:index + width]
    # Vertical pass: pad the height only, convolve along the first axis.
    padded = np.pad(horizontal, ((radius, radius), (0, 0)), mode="symmetric")
    vertical = np.zeros((height, width), dtype=np.float64)
    for index, weight in enumerate(kernel):
        vertical += weight * padded[index:index + height, :]
    return vertical


def roi_slice(shape: tuple[int, int], roi: Sequence[float]) -> tuple[int, int, int, int]:
    """Normalised (x0, y0, x1, y1) -> pixel slices (y0, y1, x0, x1)."""
    height, width = shape
    x0, y0, x1, y1 = roi
    return (int(y0 * height), int(y1 * height), int(x0 * width), int(x1 * width))


def resample_rgb(rgb: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Resample an RGB array to (height, width) with a lanczos filter."""
    height, width = size
    current = (rgb.shape[0], rgb.shape[1])
    if current == (height, width):
        return rgb
    image = Image.fromarray(np.ascontiguousarray(rgb.astype(np.uint8)))
    resized = image.resize((width, height), Image.LANCZOS)
    return np.asarray(resized)


# --------------------------------------------------------------------------
# pore band
# --------------------------------------------------------------------------
def pore_band_metrics(gray: np.ndarray) -> dict:
    """Pore-band energy plus the two ratios that tell noise from a pore."""
    pore = band_energy(gray, PORE_BAND[0], PORE_BAND[1])
    spec = band_energy(gray, SPEC_BAND[0], SPEC_BAND[1])
    blurred = gaussian_blur(gray, BLUR_SIGMA)
    pore_blurred = band_energy(blurred, PORE_BAND[0], PORE_BAND[1])
    return {
        "pore": float(pore),
        "spec": float(spec),
        "pore_ratio": float(pore / max(spec, 1e-9)),
        "survival": float(pore_blurred / max(pore, 1e-9)),
        "lap": float(np.abs(laplacian(gray)).mean()),
    }


def measure_crops(crop_paths: Sequence[str], canonical_size: tuple[int, int] | None = None) -> dict:
    """Aggregate pore metrics over a set of skin crops.

    ``canonical_size`` is the pixel size every crop is resampled to before
    the spectrum is measured. The pore band is defined in pixels, so crops
    taken from clips of different resolutions are otherwise not comparable -
    the same trap that ``compare_video_texture`` handles for its ROIs.
    """
    rows: list[dict] = []
    for path in crop_paths:
        rgb = np.asarray(Image.open(path).convert("RGB"))
        if canonical_size is not None:
            rgb = resample_rgb(rgb, canonical_size)
        gray = rgb.astype(np.float64).mean(axis=2)
        rows.append(pore_band_metrics(gray))
    if not rows:
        return {
            "pore": 0.0, "spec": 0.0, "pore_ratio": 0.0, "survival": 0.0,
            "lap": 0.0, "crops": 0,
        }
    keys = ("pore", "spec", "pore_ratio", "survival", "lap")
    out = {key: float(np.mean([row[key] for row in rows])) for key in keys}
    out["survival_min"] = float(np.min([row["survival"] for row in rows]))
    out["pore_min"] = float(np.min([row["pore"] for row in rows]))
    out["crops"] = len(rows)
    return out


# --------------------------------------------------------------------------
# temporal metrics
# --------------------------------------------------------------------------
def temporal_metrics(
    frame_paths: Sequence[str],
    face_roi: Sequence[float] | None = DEFAULT_FACE_ROI,
) -> dict:
    """Frame-to-frame stability of a clip.

    Same definitions as ``scripts/compare_video_texture.py``:

    * ``rgb_delta`` - mean |RGB(frame) - RGB(previous frame)|;
    * ``hf_delta``  - mean |laplacian(frame) - laplacian(previous frame)|;
    * ``face_drift`` - mean |RGB(frame) - RGB(first frame)| inside the face ROI.
    """
    rgbs: list[float] = []
    hfs: list[float] = []
    drifts: list[float] = []
    previous: np.ndarray | None = None
    previous_gray: np.ndarray | None = None
    face_reference: np.ndarray | None = None

    for path in frame_paths:
        array = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
        gray = array.mean(axis=2)
        if previous is not None:
            rgbs.append(float(np.abs(array - previous).mean()))
        if previous_gray is not None:
            hfs.append(float(np.abs(laplacian(previous_gray) - laplacian(gray)).mean()))
        if face_roi:
            y0, y1, x0, x1 = roi_slice(array.shape[:2], face_roi)
            patch = array[y0:y1, x0:x1]
            if face_reference is None:
                face_reference = patch
            else:
                drifts.append(float(np.abs(patch - face_reference).mean()))
        previous, previous_gray = array, gray

    return {
        "rgb_delta": float(np.mean(rgbs)) if rgbs else 0.0,
        "hf_delta": float(np.mean(hfs)) if hfs else 0.0,
        "face_drift": float(np.mean(drifts)) if drifts else 0.0,
        "face_drift_max": float(np.max(drifts)) if drifts else 0.0,
        "frames": len(frame_paths),
    }


# --------------------------------------------------------------------------
# comparability
# --------------------------------------------------------------------------
# A candidate whose frame rate differs from the reference is not a re-render
# of the same motion: RIFE at 48 fps halves the frame-to-frame delta by
# construction, which is a change of sampling, not an improvement.
FPS_TOLERANCE = 0.5
# Frame counts have to match within this ratio.
FRAME_COUNT_TOLERANCE = 0.1
# A temporal delta this many times the reference's is a different take, not
# flicker. Measured on re-renders of the same seed the ratio stays near 1.
MOTION_DELTA_FACTOR = 2.0
# The same argument applies in the other direction: a candidate whose
# temporal delta is a fraction of the reference's is not "less flicker", it
# is different (or much less) motion. A static candidate against a moving
# reference has a ratio of ~0 and must not get a numeric flicker verdict.
MOTION_DELTA_MIN_RATIO = 0.5
# Below this frame correlation the two clips are not the same content.
MIN_FRAME_CORRELATION = 0.5


def check_comparability(
    reference_info: dict,
    candidate_info: dict,
    reference_temporal: dict,
    candidate_temporal: dict,
    correlation: float | None = None,
) -> dict:
    """Decide whether the flicker numbers of a candidate mean anything.

    Returns a dict with ``comparable`` (bool), ``reason`` (a sentence, or an
    empty string) and the individual checks, so the report can print
    "not comparable" WITH the explanation instead of a number that looks
    like a verdict but is not one.
    """
    reasons: list[str] = []
    checks: dict = {}

    ref_fps = float(reference_info.get("fps") or 0.0)
    cand_fps = float(candidate_info.get("fps") or 0.0)
    fps_delta = abs(cand_fps - ref_fps)
    checks["fps_delta"] = round(fps_delta, 4)
    if fps_delta > FPS_TOLERANCE:
        reasons.append(
            f"frame rate differs from the reference ({cand_fps:g} vs {ref_fps:g} fps), "
            "so the frame-to-frame delta measures a different sampling"
        )

    ref_frames = int(reference_info.get("frames") or 0)
    cand_frames = int(candidate_info.get("frames") or 0)
    frame_ratio = (cand_frames / ref_frames) if ref_frames and cand_frames else None
    checks["frame_ratio"] = round(frame_ratio, 4) if frame_ratio else None
    if frame_ratio is not None and abs(frame_ratio - 1.0) > FRAME_COUNT_TOLERANCE:
        reasons.append(
            f"frame count differs from the reference ({cand_frames} vs {ref_frames}), "
            "so the clips are not the same take"
        )

    ref_delta = float(reference_temporal.get("rgb_delta") or 0.0)
    cand_delta = float(candidate_temporal.get("rgb_delta") or 0.0)
    checks["reference_rgb_delta"] = round(ref_delta, 4)
    checks["candidate_rgb_delta"] = round(cand_delta, 4)
    if ref_delta <= 1e-9 and cand_delta <= 1e-9:
        # Both are static: there is no motion to compare, and no flicker
        # number could mean anything either.
        delta_ratio = 1.0
        checks["rgb_delta_ratio"] = 1.0
        reasons.append(
            "neither clip has any measurable temporal motion, so there is no "
            "flicker to compare"
        )
    else:
        delta_ratio = (cand_delta / ref_delta) if ref_delta > 1e-9 else None
        checks["rgb_delta_ratio"] = round(delta_ratio, 4) if delta_ratio else None
        if delta_ratio is not None and delta_ratio > MOTION_DELTA_FACTOR:
            reasons.append(
                f"temporal RGB delta is {delta_ratio:.1f}x the reference, which means "
                "the motion itself differs - this is a different take, not flicker"
            )
        elif delta_ratio is not None and delta_ratio < MOTION_DELTA_MIN_RATIO:
            reasons.append(
                f"temporal RGB delta is only {delta_ratio:.2f}x the reference, so the "
                "candidate has different (much smaller) motion - a different take, "
                "not an improvement in flicker"
            )
        elif delta_ratio is None:
            reasons.append(
                "the reference has no measurable temporal motion while the candidate "
                "does, so the two clips do not have the same motion"
            )

    if correlation is not None:
        checks["frame_correlation"] = round(float(correlation), 4)
        if correlation < MIN_FRAME_CORRELATION:
            reasons.append(
                f"frames correlate with the reference at only {correlation:.2f}, "
                "so the two clips are not the same content"
            )

    return {
        "comparable": not reasons,
        "status": "comparable" if not reasons else "not comparable",
        "reason": "; ".join(reasons),
        "checks": checks,
    }


# --------------------------------------------------------------------------
# aggregation of the external judge's scores
# --------------------------------------------------------------------------
# The four criteria the vision judge is asked about. ``higher_is_better``
# is False for the two defect scales (10 = worst).
CRITERIA: dict[str, dict] = {
    "pore_readability": {
        "higher_is_better": True,
        "prompt": "readability of the pores (1-10)",
        "aliases": ("pores", "pore", "pore_readability", "pores_readability"),
    },
    "naturalness": {
        "higher_is_better": True,
        "prompt": "naturalness of the texture (1-10)",
        "aliases": ("natural", "naturalness", "texture_naturalness"),
    },
    "wax_plastic": {
        "higher_is_better": False,
        "prompt": "wax / plastic look (10 = maximum defect)",
        "aliases": ("wax", "wax_plastic", "waxy", "plastic", "wax_plastic_defect"),
    },
    "overlay_noise": {
        "higher_is_better": False,
        "prompt": "overlay noise (10 = maximum)",
        "aliases": ("noise", "overlay_noise", "sand", "grain"),
    },
}


def canonical_criterion(name: str) -> str | None:
    """Map a judge's criterion name onto one of :data:`CRITERIA`."""
    key = str(name).strip().lower().replace(" ", "_").replace("-", "_")
    if key in CRITERIA:
        return key
    for criterion, spec in CRITERIA.items():
        if key in spec["aliases"]:
            return criterion
    return None


def median(values: Iterable[float]) -> float:
    data = sorted(float(v) for v in values)
    if not data:
        return 0.0
    middle = len(data) // 2
    if len(data) % 2:
        return data[middle]
    return (data[middle - 1] + data[middle]) / 2.0


def quartile(values: Iterable[float], fraction: float) -> float:
    """Linear-interpolation quantile (same convention as numpy's default)."""
    data = sorted(float(v) for v in values)
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]
    position = (len(data) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return data[int(position)]
    return data[lower] + (data[upper] - data[lower]) * (position - lower)


def iqr(values: Iterable[float]) -> float:
    data = list(values)
    return quartile(data, 0.75) - quartile(data, 0.25)
