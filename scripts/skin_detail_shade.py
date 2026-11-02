"""Lighting-aware skin detail transfer: real pore relief, lit by the target's own light.

Plain texture transfer (adding the reference's high-frequency luminance) fails: the stamped
pattern reads as dirt or scratches because it carries the reference's own lighting and does
not respond to the target's shading.

Here the reference is used only as a HEIGHT field. We build a micro-normal map from it and
re-shade the target using the target's own light direction (estimated from the gradient of
its blurred luminance). Pores then darken on the shadow side and brighten on the lit side,
exactly like real skin, and no foreign pattern is stamped onto the image.

Shape, colour and composition are untouched - only luminance inside the skin mask changes.
"""
from __future__ import annotations

import argparse
import os

import numpy as np
from PIL import Image


def blur(a: np.ndarray, k: int = 9) -> np.ndarray:
    """Separable box blur via cumulative sums (no scipy)."""
    if k < 3:
        return a
    pad = k // 2
    out = np.pad(a, pad, mode="edge")
    c = np.cumsum(out, axis=0)
    c = np.vstack([np.zeros((1, c.shape[1]), dtype=c.dtype), c])
    a = (c[k:, :] - c[:-k, :]) / k
    c = np.cumsum(a, axis=1)
    c = np.hstack([np.zeros((c.shape[0], 1), dtype=c.dtype), c])
    a = (c[:, k:] - c[:, :-k]) / k
    return a


def skin_mask(rgb: np.ndarray) -> np.ndarray:
    """Soft skin-chroma mask, same calibration as skin_grade.py."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 0.5 * (b - y) + 0.5
    cr = 0.5 * (r - y) + 0.5
    # широкие диапазоны: после тёплой цветокоррекции кожа уходит в оранжевый
    # (g-b до ~0.15), и строгий маск skin_grade.py её терял (покрытие 5.8% вместо ~40%)
    # Окна сняты с реальных замеров ПОСЛЕ тёплой цветокоррекции:
    #   кожа тела: r-g ~0.26, g-b ~0.23, cb ~0.385, cr ~0.63
    #   серая ткань/фон: r-g ~0.00, g-b ~0.02, cb ~0.50, cr ~0.50
    # Разделитель - именно r-g (кожа всегда заметно теплее серого).
    m = np.clip((cb - 0.33) / 0.05, 0, 1) * np.clip((0.56 - cb) / 0.05, 0, 1)
    m *= np.clip((cr - 0.50) / 0.04, 0, 1) * np.clip((0.72 - cr) / 0.06, 0, 1)
    m *= np.clip((r - g - 0.09) / 0.04, 0, 1)
    m *= np.clip((g - b + 0.02) / 0.05, 0, 1) * np.clip((0.32 - (g - b)) / 0.08, 0, 1)
    m *= np.clip((y - 0.05) / 0.08, 0, 1)
    return m


def height_from_ref(path: str, relief: int = 5) -> np.ndarray:
    """Height field from the reference: luminance minus its own lighting.

    The raw band-pass is dominated by the DARK RIMS around the pores, and transferring
    them produces etched-looking loops instead of skin. So the field is normalised by a
    robust percentile (not std) and hard-clipped: only the mid-level micro-relief that
    actually behaves like skin survives, the heavy rims are dropped.
    """
    im = Image.open(path).convert("L")
    a = np.asarray(im).astype(np.float32) / 255.0
    # kill the reference's own low-frequency lighting, keep the pore-scale relief
    h = a - blur(a, relief * 2 + 1)
    h -= h.mean()
    scale = float(np.percentile(np.abs(h), 88))
    if scale < 1e-6:
        scale = float(h.std()) + 1e-6
    h = h / scale
    np.clip(h, -1.4, 1.4, out=h)          # drop the extreme dark rims / bright halos
    return h.astype(np.float32)


def apply_detail(src: str, ref: str, dst: str, strength: float = 0.035,
                 relief: int = 5, mask_k: int = 15, height: int = 1):
    im = Image.open(src).convert("RGB")
    a = np.asarray(im).astype(np.float32) / 255.0
    h, w, _ = a.shape

    mask = blur(skin_mask(a), mask_k)[..., None]

    hmap = height_from_ref(ref, relief)
    # tile the reference relief over the target (it is a texture, not a picture)
    th, tw = hmap.shape
    reps_y = int(np.ceil(h / th)) + 1
    reps_x = int(np.ceil(w / tw)) + 1
    tiled = np.tile(hmap, (reps_y, reps_x))[:h, :w]
    # rotate the tile per quadrant so the repeat is not obvious
    hh, ww = h // 2, w // 2
    tiled[hh:, :ww] = np.rot90(tiled[hh:, :ww], 2)

    # micro-normal from the relief
    gy, gx = np.gradient(tiled)
    gx /= (np.abs(gx).max() + 1e-6)
    gy /= (np.abs(gy).max() + 1e-6)
    nz = np.ones_like(gx)
    nx, ny = -gx * 0.35, -gy * 0.35
    ln = np.sqrt(nx * nx + ny * ny + nz * nz)
    nx, ny, nz = nx / ln, ny / ln, nz / ln

    # target's own light direction from the gradient of its blurred luminance
    lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    lb = blur(lum, 25)
    ly, lx = np.gradient(lb)
    scale = np.sqrt(lx * lx + ly * ly)
    scale /= (scale.mean() + 1e-6)
    lx, ly = lx / (scale + 1e-6), ly / (scale + 1e-6)
    lz = np.ones_like(lx)
    ll = np.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx / ll, ly / ll, lz / ll

    shade = nx * lx + ny * ly + nz * lz          # dot(light, normal)
    shade = shade - shade.mean()
    shade /= (shade.std() + 1e-6)
    delta = (strength * shade * mask[..., 0])[..., None]

    out = np.clip(a + delta, 0, 1)
    Image.fromarray((out * 255).astype(np.uint8)).save(dst)
    return float(mask.mean())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--strength", type=float, default=0.035)
    ap.add_argument("--relief", type=int, default=5)
    args = ap.parse_args()
    cover = apply_detail(args.src, args.ref, args.out, args.strength, args.relief)
    print(f"готово: {args.out} | кожа в маске: {cover*100:.1f}%")
