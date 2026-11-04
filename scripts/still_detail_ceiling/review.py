"""Deterministic skin review crop and native-resolution contact sheet."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from scripts.pore_injector.mask import fallback_skin_mask


def choose_review_roi(image: np.ndarray, size: int = 256) -> tuple[int, int, int, int]:
    """Choose a skin-rich square, breaking ties toward the image centre."""
    height, width = image.shape[:2]
    size = min(int(size), height, width)
    if size <= 0:
        raise ValueError("review size must be positive")
    max_x, max_y = width - size, height - size
    if max_x == 0 or max_y == 0:
        return (0, 0, width, height)

    frame = image.astype(np.float32) / 255.0
    mask, _ = fallback_skin_mask(frame)
    mask = mask > 0.5
    if not mask.any() or mask.all():
        return ((width - size) // 2, (height - size) // 2,
                (width - size) // 2 + size, (height - size) // 2 + size)
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    stride = max(1, size // 4)

    def positions(limit: int) -> list[int]:
        values = list(range(0, limit + 1, stride))
        if values[-1] != limit:
            values.append(limit)
        return values

    centre_x, centre_y = max_x / 2.0, max_y / 2.0
    best = None
    for y0 in positions(max_y):
        for x0 in positions(max_x):
            crop_mask = mask[y0:y0 + size, x0:x0 + size]
            coverage = float(crop_mask.mean())
            detail = float(gray[y0:y0 + size, x0:x0 + size].std())
            centre_distance = ((x0 - centre_x) ** 2 + (y0 - centre_y) ** 2) ** 0.5
            crop_centre_x = x0 + size / 2.0
            crop_centre_y = y0 + size / 2.0
            upper_skin_prior = float(np.exp(
                -((crop_centre_x - width / 2.0) / max(width * 0.30, 1.0)) ** 2
                -((crop_centre_y - height * 0.28) / max(height * 0.25, 1.0)) ** 2
            ))
            score = 0.64 * coverage + 0.31 * upper_skin_prior + 0.05 * min(detail / 32.0, 1.0)
            key = (score, -centre_distance)
            if best is None or key > best[0]:
                best = (key, (x0, y0, x0 + size, y0 + size))
    assert best is not None
    return best[1]


def crop_at_source_roi(
    image: np.ndarray,
    roi: tuple[int, int, int, int],
    source_shape: tuple[int, int],
) -> np.ndarray:
    """Apply normalized source ROI coordinates to a candidate image."""
    source_height, source_width = source_shape
    height, width = image.shape[:2]
    x0, y0, x1, y1 = roi
    left = round(x0 * width / source_width)
    top = round(y0 * height / source_height)
    right = round(x1 * width / source_width)
    bottom = round(y1 * height / source_height)
    crop = image[top:bottom, left:right]
    if not crop.size:
        raise ValueError("review crop is empty")
    return crop


def write_review_sheet(
    candidates: dict[str, np.ndarray],
    output_path: Path,
    roi: tuple[int, int, int, int],
    source_shape: tuple[int, int],
) -> None:
    """Write same-ROI native-target crops without another detail-changing resize."""
    ordered = [
        name for name in ("bicubic", "lanczos", "seedvr2", "real_esrgan", "codeformer",
                          "pore_minimax", "pore_kimi")
        if name in candidates
    ]
    crops = [crop_at_source_roi(candidates[name], roi, source_shape) for name in ordered]
    crop_height = max(crop.shape[0] for crop in crops)
    crop_width = max(crop.shape[1] for crop in crops)
    label_height = 30
    sheet = np.zeros((crop_height + label_height, crop_width * len(crops), 3), dtype=np.uint8)
    for index, (name, crop) in enumerate(zip(ordered, crops)):
        x0 = index * crop_width
        sheet[label_height:label_height + crop.shape[0], x0:x0 + crop.shape[1]] = crop
        cv2.putText(
            sheet, name, (x0 + 5, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
            (255, 255, 255), 1, cv2.LINE_AA,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bgr = cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR)
    if not cv2.imwrite(str(output_path), bgr):
        raise RuntimeError("could not write review sheet")
