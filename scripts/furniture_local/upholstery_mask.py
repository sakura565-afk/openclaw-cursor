"""Маска обивки по цвету: bbox ∩ «светлая низко-насыщенная ткань».

Зачем: bbox-маска на реальных фото захватывает посторонние объекты —
декоративные подушки, деревянные ножки, мраморный столец. Отбор по цвету
ткани отсекает их автоматически, без ручной разметки.

Эталон обивки по ТЗ FURN-SRC-20261122: светло-бежевый/овсяный лён.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

try:
    from scipy import ndimage

    _HAS_SCIPY = True
except ImportError:  # pragma: no cover
    _HAS_SCIPY = False


def _hsv_masks(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    dd = mx - mn
    hue = np.zeros(mx.shape, dtype=np.float32)
    valid = dd > 1e-6
    if valid.any():
        v = mx[valid]
        rr, gg, bb = r[valid], g[valid], b[valid]
        d = dd[valid]
        hue[valid] = np.where(
            v == rr,
            (60 * ((gg - bb) / d) + 360) % 360,
            np.where(v == gg, (60 * ((bb - rr) / d) + 120) % 360, (60 * ((rr - gg) / d) + 240) % 360),
        )
    sat = np.where(mx > 0, dd / np.maximum(mx, 1), 0)
    return hue, sat


def build_upholstery_mask(
    image_path: Path,
    bbox: tuple[int, int, int, int],
    output_path: Path,
    *,
    preview_path: Path | None = None,
    lum_lo: float = 95.0,
    lum_hi: float = 235.0,
    sat_max: float = 0.30,
    hue_lo: float = 15.0,
    hue_hi: float = 60.0,
    feather: float = 10.0,
    min_component_px: int = 300,
    grow_by: int = 0,
) -> dict:
    """Собрать маску обивки. Белое = перекрашивать."""
    with Image.open(image_path) as im:
        rgb_img = im.convert("RGB")
    rgb = np.asarray(rgb_img).astype(np.float32)
    hue, sat = _hsv_masks(rgb)
    lum = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]

    ok = (lum >= lum_lo) & (lum <= lum_hi) & (sat <= sat_max) & (hue >= hue_lo) & (hue <= hue_hi)

    x0, y0, x1, y1 = bbox
    box = np.zeros(ok.shape, dtype=bool)
    box[max(0, y0) : y1, max(0, x0) : x1] = True
    ok = ok & box

    # Убираем шум: связные компоненты мельче min_component_px.
    if _HAS_SCIPY:
        lab, n = ndimage.label(ok)
        if n:
            sizes = ndimage.sum(ok, lab, range(1, n + 1))
            keep = np.zeros(n + 1, dtype=bool)
            keep[1:] = sizes >= min_component_px
            ok = keep[lab]
    else:
        eroded = np.asarray(
            Image.fromarray((ok * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3))
        ) > 127
        ok = ok & eroded

    if grow_by > 0:
        grown = Image.fromarray((ok * 255).astype(np.uint8)).filter(
            ImageFilter.MaxFilter(grow_by * 2 + 1)
        )
        ok = np.asarray(grown) > 127

    mask = Image.fromarray((ok * 255).astype(np.uint8))
    if feather > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=feather))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mask.save(output_path)

    if preview_path is not None:
        vis = np.asarray(rgb_img).copy()
        edge = ok & ~(np.asarray(mask) > 127)
        vis[edge] = [255, 0, 0]
        Image.fromarray(vis).save(preview_path)

    arr = np.asarray(mask)
    ys, xs = np.nonzero(arr > 127)
    box_out = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if xs.size else [0, 0, 0, 0]
    return {
        "path": str(output_path),
        "bbox": box_out,
        "area": int((arr > 127).sum()),
        "coverage_in_bbox": round(float((arr[y0:y1, x0:x1] > 127).mean()), 4),
        "scipy": _HAS_SCIPY,
    }
