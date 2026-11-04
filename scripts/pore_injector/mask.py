# -*- coding: utf-8 -*-
"""Skin mask for the pore injector: external PNG, or a colour prior fallback.

Two things this module exists to get right
------------------------------------------
1. The float32 trap. Frames here are float32 in [0, 1]. ``cv2.cvtColor`` on
   such an array treats the values as if they were 0..255, which collapses
   saturation and value towards zero and leaves every threshold comparison
   dead. Our old injector had exactly that bug: the mask was always 0%
   coverage, so every ``--strength`` produced a byte-identical file. Every
   colour computation here therefore goes through an explicit uint8 copy.

2. The 84% coverage trap. A warm-hue HSV prior alone also matches warm
   plaster, tan brick and warm-lit foam, and on our bath clip it covered
   85% of the frame. Pores in a wall are worse than no pores. The prior
   below therefore combines hue with the YCrCb skin box and the R >= G >= B
   ordering, then keeps only the largest connected component.

Foam is rejected explicitly: foam is bright AND desaturated, so
``v > 0.95`` or ``s < 0.10`` is out.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np

__all__ = ["MaskStats", "fallback_skin_mask", "load_external_mask", "mask_series"]


class MaskStats(dict):
    """Coverage numbers, kept as plain data so they can go into the report."""


def _to_uint8(frame: np.ndarray) -> np.ndarray:
    """Convert a float32 [0, 1] frame to uint8 for OpenCV colour spaces.

    This is the fix for the "mask is always empty" bug: every threshold in
    OpenCV's HSV/YCrCb output is calibrated for 0..255.
    """
    if frame.dtype == np.uint8:
        return frame
    return (np.clip(frame, 0.0, 1.0) * 255.0).astype(np.uint8)


def _fill_small_holes(mask: np.ndarray, max_hole_fraction: float = 0.05) -> np.ndarray:
    """Close small enclosed holes, but NOT large ones.

    This distinction was measured, not guessed. Flood-filling the complement
    from the border and restoring everything it reaches fills the bathtub
    interior as well, because the body surrounds it: the tub region went from
    17% selected to 100% selected. Restricting the fill to holes smaller than
    a fraction of the frame keeps skin creases and shadow gaps closed while
    leaving the tub alone.
    """
    height, width = mask.shape
    flood = np.zeros((height + 2, width + 2), np.uint8)
    image = (1 - mask).copy()
    cv2.floodFill(image, flood, (0, 0), 2)
    holes = image == 2

    count, labels, stats, _ = cv2.connectedComponentsWithStats(holes.astype(np.uint8), 4)
    keep = np.zeros_like(holes)
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] <= max_hole_fraction * mask.size:
            keep |= labels == index
    return ((mask > 0) | keep).astype(np.uint8)


def _largest_component(mask: np.ndarray) -> np.ndarray:
    """Keep only the biggest 8-connected blob."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if count <= 1:
        return mask
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return (labels == biggest).astype(np.uint8)


def fallback_skin_mask(
    frame: np.ndarray,
    smooth_sigma: float = 2.0,
    min_saturation: float = 0.10,
    max_value: float = 0.95,
    min_value: float = 0.08,
    cr_range: tuple[float, float] = (145.0, 180.0),
    cb_range: tuple[float, float] = (70.0, 125.0),
    min_red_green: float = 0.15,
    open_kernel: int = 9,
    close_kernel: int = 5,
    max_hole_fraction: float = 0.05,
    min_component_fraction: float = 0.01,
    largest_component_only: bool = True,
) -> tuple[np.ndarray, MaskStats]:
    """Colour prior for skin, computed on a float32 [0, 1] frame.

    The thresholds were tuned against measured region statistics of the bath
    clip (skin vs wall vs brick vs tub vs foam vs hair), not by eye. On that
    clip this selects 50% of the frame with the wall at 0%, and it is the
    reason the fallback does not become an 85% catch-all.

    Returns the soft mask (0..1) and the coverage statistics.
    """
    if frame.ndim != 3 or frame.shape[2] < 3:
        raise ValueError(f"expected an RGB frame, got shape {frame.shape}")

    u8 = _to_uint8(frame[:, :, :3])
    hsv = cv2.cvtColor(u8, cv2.COLOR_RGB2HSV)
    h = hsv[:, :, 0].astype(np.float32) * 2.0   # 0..360 degrees
    s = hsv[:, :, 1].astype(np.float32) / 255.0  # 0..1
    v = hsv[:, :, 2].astype(np.float32) / 255.0  # 0..1

    r = frame[:, :, 0].astype(np.float32)
    g = frame[:, :, 1].astype(np.float32)

    # Foam and blown-out highlights: bright and desaturated, or simply too
    # bright to be skin in this scene.
    not_foam = (v <= max_value) & (s >= min_saturation) & (v >= min_value)

    # Skin sits on the warm side of the hue wheel.
    warm_hue = (h <= 50.0) | (h >= 160.0)

    # The YCrCb skin box (Chai & Ngan), narrowed at the low end because the
    # warm plaster in the background lands inside the classic 133..173 Cr
    # range and would otherwise be accepted.
    ycrcb = cv2.cvtColor(u8, cv2.COLOR_RGB2YCrCb).astype(np.float32)
    cr = ycrcb[:, :, 1]
    cb = ycrcb[:, :, 2]
    skin_box = (cr >= cr_range[0]) & (cr <= cr_range[1]) & (cb >= cb_range[0]) & (cb <= cb_range[1])

    # Skin is redder than green; warm plaster and grey hair are not. The
    # measured gap is large: skin r-g is 0.29..0.35, plaster 0.07, hair 0.07.
    red_order = (r - g) >= min_red_green

    candidate = (not_foam & warm_hue & skin_box & red_order).astype(np.uint8)

    # Close small holes, then drop isolated specks and thin bridges to the
    # background.
    if close_kernel > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_kernel, close_kernel))
        candidate = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, kernel)
    if open_kernel > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_kernel, open_kernel))
        candidate = cv2.morphologyEx(candidate, cv2.MORPH_OPEN, kernel)

    coverage_before = float(np.count_nonzero(candidate)) / float(candidate.size)

    if largest_component_only and candidate.any():
        biggest_area = int(cv2.connectedComponentsWithStats(candidate, 8)[2][1:, cv2.CC_STAT_AREA].max())
        if biggest_area >= min_component_fraction * candidate.size:
            candidate = _largest_component(candidate)
            candidate = _fill_small_holes(candidate, max_hole_fraction)

    soft = cv2.GaussianBlur(candidate.astype(np.float32), (0, 0), smooth_sigma)
    soft = np.clip(soft * 1.6, 0.0, 1.0)

    stats_out = MaskStats(
        {
            "mode": "fallback_prior",
            "coverage_before_largest_pct": coverage_before * 100.0,
            "coverage_pct": float(np.count_nonzero(soft > 0.5)) / float(soft.size) * 100.0,
            "soft_mean": float(soft.mean()),
        }
    )
    return soft, stats_out


def load_external_mask(
    path: str | os.PathLike[str],
    shape: tuple[int, int],
) -> tuple[np.ndarray, MaskStats]:
    """Load a mask PNG and fit it to the frame geometry.

    A single PNG is used for every frame. The image is interpreted as
    grayscale; anything with three or four channels is reduced to luma.
    Values are read as 0..255 and normalised, and a mask whose maximum is
    below 1.0 is treated as already normalised.
    """
    mask_path = Path(path)
    if not mask_path.exists():
        raise FileNotFoundError(f"mask not found: {mask_path}")

    raw = cv2.imread(str(mask_path), cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise ValueError(f"cannot read mask image: {mask_path}")

    if raw.ndim == 3:
        if raw.shape[2] == 4:
            raw = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
        else:
            raw = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
    gray = raw.astype(np.float32)
    peak = float(gray.max()) if gray.size else 0.0
    if peak > 1.0:
        gray /= 255.0
    gray = np.clip(gray, 0.0, 1.0)

    height, width = shape
    if gray.shape[:2] != (height, width):
        gray = cv2.resize(gray, (width, height), interpolation=cv2.INTER_LINEAR)

    stats = MaskStats(
        {
            "mode": "external_png",
            "path": str(mask_path),
            "coverage_pct": float(np.count_nonzero(gray > 0.5)) / float(gray.size) * 100.0,
            "soft_mean": float(gray.mean()),
        }
    )
    return gray, stats


def mask_series(
    mask_path: str | os.PathLike[str] | None,
    frames: list[np.ndarray],
    smooth_sigma: float = 2.0,
) -> tuple[list[np.ndarray], MaskStats]:
    """Build the per-frame mask list.

    With ``mask_path`` set to a PNG, that mask is applied to every frame.
    With it set to a directory, one PNG per frame is used, sorted by name.
    Without it, the colour prior runs per frame.
    """
    if not frames:
        raise ValueError("no frames to mask")

    shape = frames[0].shape[:2]

    if mask_path is not None:
        mask_file = Path(mask_path)
        if mask_file.is_dir():
            pngs = sorted(mask_file.glob("*.png"))
            if not pngs:
                raise FileNotFoundError(f"no PNG masks found in {mask_file}")
            masks: list[np.ndarray] = []
            for i in range(len(frames)):
                png = pngs[min(i, len(pngs) - 1)]
                one, stats = load_external_mask(png, shape)
                masks.append(one)
            summary = MaskStats(
                {
                    "mode": "external_png_sequence",
                    "path": str(mask_file),
                    "frames": len(masks),
                    "coverage_pct": float(np.mean([m.mean() for m in masks]) * 100.0),
                    "soft_mean": float(np.mean([m.mean() for m in masks])),
                }
            )
            return masks, summary

        mask, stats = load_external_mask(mask_file, shape)
        return [mask.copy() for _ in frames], stats

    masks = []
    coverages = []
    for frame in frames:
        mask, stats = fallback_skin_mask(frame, smooth_sigma=smooth_sigma)
        masks.append(mask)
        coverages.append(float(stats["coverage_pct"]))
    summary = MaskStats(
        {
            "mode": "fallback_prior",
            "coverage_pct": float(np.mean(coverages)) if coverages else 0.0,
            "coverage_pct_min": float(np.min(coverages)) if coverages else 0.0,
            "coverage_pct_max": float(np.max(coverages)) if coverages else 0.0,
            "soft_mean": float(np.mean([float(m.mean()) for m in masks])) if masks else 0.0,
        }
    )
    return masks, summary
