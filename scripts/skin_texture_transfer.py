"""Real-photographic skin texture transfer - the fix that needs no generator.

Qwen-Image-Edit 2509 cannot draw pores at all (verified across cfg 1.0-4.0, denoise
0.25-1.0, four resolutions and three skin/detail LoRAs), and every skin LoRA makes the
1:1 skin worse (stubble, grit, blotchy pigment). SeedVR2 only sharpens.

So instead of asking a model for micro-texture, take REAL micro-texture from an actual
photograph and composite it: high-frequency (detail) layer of a real skin close-up is
added to the render's detail layer, luminance only, inside the skin mask. This gives
authentic pore/furrow structure with zero generation artifacts, and it cannot move the
body, the face or the colours.

The reference patch is tiled at 1:1 and mirrored between tiles to avoid an obvious
repeat. The mask is eroded so silhouette edges stay clean.
"""
from __future__ import annotations

import argparse
import os

import numpy as np
from PIL import Image


def box_blur(a: np.ndarray, k: int) -> np.ndarray:
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
    return (c[:, k:] - c[:, :-k]) / k


def erode(mask: np.ndarray, k: int) -> np.ndarray:
    """Min-filter approximation: blur then re-threshold -> shrinks the mask."""
    b = box_blur(mask, k)
    return np.clip((b - 0.5) * 6.0 + 0.5, 0, 1)


def skin_mask(rgb: np.ndarray) -> np.ndarray:
    r = rgb[..., 0]
    g = rgb[..., 1]
    b = rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 0.5 * (b - y) + 0.5
    cr = 0.5 * (r - y) + 0.5
    # Тон-независимые признаки: r-g и g-b. Cb/Cr-плашки работают только на исходном
    # тоне (после цветокоррекции Cb сдвигается 0.46 -> 0.39 и маска сходит на нет).
    warm = np.clip((r - g - 0.045) / 0.035, 0, 1)
    warm2 = np.clip((g - b - 0.005) / 0.03, 0, 1)
    m = warm * warm2
    m *= np.clip((y - 0.06) / 0.08, 0, 1)
    m *= np.clip((0.95 - y) / 0.08, 0, 1)
    return m


def luma(rgb: np.ndarray) -> np.ndarray:
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def build_tiled_detail(ref_rgb: np.ndarray, h: int, w: int, tile: int = 400) -> np.ndarray:
    """Tile the reference high-frequency layer over an h x w canvas (mirrored)."""
    ref_y = luma(ref_rgb)
    # high-pass of the reference at skin scale
    base = box_blur(ref_y, 9)
    hp = ref_y - base
    # remove the reference's own low-frequency lighting so only texture transfers
    hp -= hp.mean()
    rp, cp = hp.shape
    if rp < tile or cp < tile:
        hp = np.resize(hp, (max(rp, tile), max(cp, tile)))
        rp, cp = hp.shape
    # pick a clean central patch
    r0 = max(0, (rp - tile) // 2)
    c0 = max(0, (cp - tile) // 2)
    patch = hp[r0:r0 + tile, c0:c0 + tile]
    # normalize to unit std so `strength` is meaningful
    s = patch.std()
    if s > 1e-6:
        patch = patch / s
    # tile with mirroring
    out = np.zeros((h, w), dtype=np.float32)
    ny = int(np.ceil(h / tile))
    nx = int(np.ceil(w / tile))
    for iy in range(ny):
        for ix in range(nx):
            p = patch
            if iy % 2 == 1:
                p = p[::-1]
            if ix % 2 == 1:
                p = p[:, ::-1]
            y0 = iy * tile
            x0 = ix * tile
            hh = min(tile, h - y0)
            ww = min(tile, w - x0)
            if hh <= 0 or ww <= 0:
                continue
            out[y0:y0 + hh, x0:x0 + ww] = p[:hh, :ww]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--strength", type=float, default=0.030,
                    help="amplitude of the transferred detail layer (in 0..1 luminance)")
    ap.add_argument("--mask-erosion", type=int, default=11)
    ap.add_argument("--mask-blur", type=int, default=9)
    ap.add_argument("--tile", type=int, default=400)
    args = ap.parse_args()

    src = np.asarray(Image.open(args.src).convert("RGB")).astype(np.float32) / 255.0
    ref = np.asarray(Image.open(args.ref).convert("RGB")).astype(np.float32) / 255.0
    h, w = src.shape[:2]
    print(f"источник {w}x{h} | референс {ref.shape[1]}x{ref.shape[0]}")

    m = box_blur(skin_mask(src), args.mask_blur)
    m = erode(m, args.mask_erosion)
    m = box_blur(m, args.mask_blur)
    print(f"покрытие маской: {m.mean()*100:.1f}%")

    # detail layer of the target (what we replace)
    y = luma(src)
    tgt_hp = y - box_blur(y, 9)
    # detail layer of the real skin
    ref_detail = build_tiled_detail(ref, h, w, args.tile)

    # luminance-only transfer, inside the skin mask
    new_y = y + args.strength * ref_detail * m
    # re-apply to RGB, preserving chroma
    delta = (new_y - y)[..., None]
    out = np.clip(src + delta, 0, 1)
    Image.fromarray((out * 255).astype(np.uint8)).save(args.out)
    print(f"готово: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
