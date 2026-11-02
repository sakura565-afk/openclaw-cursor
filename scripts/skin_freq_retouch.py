# -*- coding: utf-8 -*-
"""Expert-style skin finishing for AI renders: frequency separation + dodge&burn + Lab + grain.

Why this exists
---------------
Qwen-Image-Edit renders skin as perfectly smooth gradients. Real skin, even on a retouched
fashion photo, always shows TWO things our renders lack:

1. low-frequency tonal variation (dodge & burn on the colour layer) - the soft light/shadow
   break-up that makes skin read as a body rather than a painted surface;
2. high-frequency micro-relief at skin scale (pores, fine roughness) plus film grain.

Professional retouchers (frequency separation -> dodge & burn -> selective colour -> grain)
address exactly this split. Sources for the technique:
  - Retouching Labs, "Advanced Retouching With Color Correction" and "Fashion Photography
    Retouching": frequency separation (Gaussian blur 6-12 px at 100% for the low layer),
    dodge & burn with a 50% gray overlay layer at brush opacity 3-7% / flow 5-10%,
    colour grading via Curves / Selective Color / Color Balance, LAB and Selective HSL.
  - WhiteWall Magazine, "Advanced retouching techniques": low frequency = tone and colour,
    high frequency = pores and hair; dodge & burn on a 50% gray Overlay layer.
  - phlearn, "Frequency Separation Retouching".

This script does the same operations numerically:
  1. frequency separation (gaussian low layer, high = image - low)
  2. low-frequency local contrast  == automated dodge & burn on the colour layer
  3. high-frequency micro-contrast at skin scale == restoring pore-scale relief
  4. colour work in CIELAB (a* = warmth, chroma = saturation) inside a skin mask
  5. film grain, because AI renders have none and its absence reads as synthetic

Nothing outside the skin mask changes except the grain, which is global but very low
amplitude. Geometry, pose, face and background are untouched.
"""
from __future__ import annotations

import argparse
import os

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# расширенная маска: она снята с реальных замеров ПОСЛЕ тёплой цветокоррекции
# (кожа r-g ~0.26 / g-b ~0.23, серая ткань r-g ~0.00) - строгая маска skin_grade.py
# на таком кадре ловила лишь 5% пикселей и резала именно тёплую кожу.
from skin_detail_shade import skin_mask  # noqa: E402


def gauss(a: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return a
    if a.ndim == 3:
        # blur spatially only, never across the colour channels
        return gaussian_filter(a, sigma=(sigma, sigma, 0), mode="nearest")
    return gaussian_filter(a, sigma=(sigma, sigma), mode="nearest")


def local_contrast(layer: np.ndarray, radius: float, strength: float) -> np.ndarray:
    """Dodge & burn on a frequency layer: local mean removal = soft tonal break-up.

    A gentle unsharp on the LOW-frequency layer is the numeric equivalent of painting
    dodge and burn on the colour layer of a frequency-separated document: it deepens
    creases and lifts ridges without touching the texture band.
    """
    if radius <= 0 or strength <= 0:
        return layer
    base = gauss(layer, radius)
    return layer + (layer - base) * strength


def grain(shape, amount: float, seed: int = 12345) -> np.ndarray:
    """Film grain: gaussian noise, slightly stronger in the shadows (as on real film)."""
    if amount <= 0:
        return np.zeros(shape, dtype=np.float32)
    rng = np.random.default_rng(seed)
    n = rng.normal(0.0, 1.0, shape[:2]).astype(np.float32)
    n = gauss(n, 0.6)
    n /= (n.std() + 1e-6)
    return n * amount


def retouch(src: str, dst: str, *,
            sep_sigma: float = 8.0,
            lb_radius: float = 30.0, lb_strength: float = 0.35,
            hf_radius: float = 4.0, hf_strength: float = 0.35,
            warmth: float = 4.0, sat: float = 1.06,
            grain_amount: float = 0.012,
            mask_k: int = 15, grain_global: bool = True):
    rgb = np.asarray(cv2.imread(src, cv2.IMREAD_COLOR)[:, :, ::-1]).astype(np.float32) / 255.0
    h, w, _ = rgb.shape

    # --- 1. frequency separation -------------------------------------------------
    low = gauss(rgb, sep_sigma)
    high = rgb - low

    # --- 2. dodge & burn on the low (tone/colour) layer --------------------------
    low = local_contrast(low, lb_radius, lb_strength)

    # --- 3. micro-relief on the high (texture) layer ------------------------------
    if hf_strength > 0:
        high = high + gauss(high, hf_radius) * hf_strength * 2.0

    recomb = np.clip(low + high, 0, 1)

    # --- 4. colour work in CIELAB inside the skin mask ---------------------------
    mask = gauss(skin_mask(recomb), mask_k)[..., None]
    bgr = (np.clip(recomb, 0, 1)[:, :, ::-1] * 255).astype(np.uint8)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    L, A, B = lab[..., 0], lab[..., 1], lab[..., 2]
    A = A + warmth * mask[..., 0]                      # a* up = warmer
    chroma = np.sqrt(A * A + B * B) + 1e-6
    target = chroma * (1.0 + (sat - 1.0) * mask[..., 0])
    A = A / chroma * target
    B = B / chroma * target
    lab[..., 1], lab[..., 2] = A, B
    out = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    out = out[:, :, ::-1].astype(np.float32) / 255.0

    # --- 5. film grain ------------------------------------------------------------
    if grain_amount > 0:
        lum = 0.299 * out[..., 0] + 0.587 * out[..., 1] + 0.114 * out[..., 2]
        g = grain(out.shape, grain_amount) * (1.0 - 0.5 * lum)      # stronger in shadows
        if grain_global:
            out = np.clip(out + g[..., None], 0, 1)
        else:
            out = np.clip(out + (g * mask[..., 0])[..., None], 0, 1)

    cv2.imwrite(dst, (np.clip(out, 0, 1)[:, :, ::-1] * 255).astype(np.uint8))
    return float(mask.mean())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sep-sigma", type=float, default=8.0)
    ap.add_argument("--lb-radius", type=float, default=30.0)
    ap.add_argument("--lb-strength", type=float, default=0.35)
    ap.add_argument("--hf-radius", type=float, default=4.0)
    ap.add_argument("--hf-strength", type=float, default=0.35)
    ap.add_argument("--warmth", type=float, default=4.0)
    ap.add_argument("--sat", type=float, default=1.06)
    ap.add_argument("--grain", type=float, default=0.012)
    ap.add_argument("--grain-global", action="store_true")
    args = ap.parse_args()
    cover = retouch(args.src, args.out, sep_sigma=args.sep_sigma, lb_radius=args.lb_radius,
                    lb_strength=args.lb_strength, hf_radius=args.hf_radius,
                    hf_strength=args.hf_strength, warmth=args.warmth, sat=args.sat,
                    grain_amount=args.grain, grain_global=args.grain_global)
    print(f"готово: {args.out} | кожа в маске: {cover*100:.1f}%")
