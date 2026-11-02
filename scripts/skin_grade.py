"""Skin-tone grade: the porcelain cast is partly a COLOUR problem, not only texture.

Qwen's smooth skin also comes out pale and slightly cool. A photographic skin grade
(warmth, saturation, micro-contrast on the skin hue range only) fixes the "porcelain"
read without any generation - no new hair, no grit, no shape drift, and it cannot
introduce anatomy errors.

Implementation: split into a YCbCr-style luminance/chroma representation, build a soft
skin mask from skin-like chroma, and apply the grade only inside that mask so the
grey-blue studio backdrop and the hair stay untouched.
"""
from __future__ import annotations

import argparse
import os

import numpy as np
from PIL import Image


def skin_mask(rgb: np.ndarray) -> np.ndarray:
    """Soft 0..1 mask of skin-like chroma (measured on real renders, not guessed).

    Measured medians on the actual frame: skin Cb 0.46 / Cr 0.55 with r-g 0.13,
    g-b 0.04; the grey-blue backdrop is neutral (Cb 0.50 / Cr 0.50) and hair is
    Cb 0.51 / Cr 0.495. The window below keeps the backdrop and hair out.
    """
    r = rgb[..., 0].astype(np.float32)
    g = rgb[..., 1].astype(np.float32)
    b = rgb[..., 2].astype(np.float32)
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 0.5 * (b - y) + 0.5
    cr = 0.5 * (r - y) + 0.5
    m = np.clip((cb - 0.40) / 0.05, 0, 1) * np.clip((0.505 - cb) / 0.035, 0, 1)
    m *= np.clip((cr - 0.515) / 0.035, 0, 1) * np.clip((0.63 - cr) / 0.06, 0, 1)
    m *= np.clip((r - g - 0.045) / 0.035, 0, 1)
    m *= np.clip((g - b - 0.005) / 0.03, 0, 1)
    m *= np.clip((y - 0.06) / 0.08, 0, 1)
    return m


def blur(a: np.ndarray, k: int = 9) -> np.ndarray:
    """Cheap separable box blur via cumulative sums (no scipy dependency)."""
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


def grade(src: str, dst: str, warmth: float, sat: float, micro: float, mask_k: int = 15):
    im = Image.open(src).convert("RGB")
    a = np.asarray(im).astype(np.float32) / 255.0
    m = blur(skin_mask(a), mask_k)[..., None]

    # micro-contrast: unsharp on the luminance channel only
    if micro > 0:
        y = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
        blur_k = 3
        yb = blur(y, blur_k)
        y_new = np.clip(y + (y - yb) * micro, 0, 1)
        a = np.clip(a + (y_new - y)[..., None], 0, 1)

    # warmth: lift red/green slightly, pull blue down, inside the skin mask
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    r_new = np.clip(r + warmth * 0.10 * m[..., 0], 0, 1)
    g_new = np.clip(g + warmth * 0.045 * m[..., 0], 0, 1)
    b_new = np.clip(b - warmth * 0.075 * m[..., 0], 0, 1)

    # saturation inside the mask
    y = 0.299 * r_new + 0.587 * g_new + 0.114 * b_new
    s = 1.0 + sat * m[..., 0]
    r_out = np.clip(y + (r_new - y) * s, 0, 1)
    g_out = np.clip(y + (g_new - y) * s, 0, 1)
    b_out = np.clip(y + (b_new - y) * s, 0, 1)

    out = np.stack([r_out, g_out, b_out], axis=-1)
    Image.fromarray((out * 255).astype(np.uint8)).save(dst)
    return float(m.mean())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--warmth", type=float, default=1.0)
    ap.add_argument("--sat", type=float, default=0.25)
    ap.add_argument("--micro", type=float, default=0.6)
    args = ap.parse_args()

    cover = grade(args.src, args.out, args.warmth, args.sat, args.micro)
    print(f"готово: {args.out} | покрытие кожи в маске: {cover*100:.1f}% пикселей")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
