# -*- coding: utf-8 -*-
"""Clean up the two artifacts a vision pass flagged on the final clip.

1. **Isolated dark specks** - the realism LoRA (and the detail blend) leave
   single-pixel dark dots on the shoulders and chest that read as digital dust
   rather than as skin features. They are removed by size: a dark connected
   component smaller than N pixels is replaced by the local surround. Real moles
   and freckles are larger clusters and survive.

2. **Painted-on specular highlights** - the wet skin shows broad, unbroken,
   almost straight highlight bands. Real wet skin breaks its highlights up. The
   fix adds a tiny, deterministic, low-frequency variation inside the clipped
   highlight regions only, so the skin keeps the wet look without the resin
   look.

Both passes are per-pixel deterministic, so they do not add flicker. The
highlight breakup uses a FIXED noise field (one seed, resampled to the frame
size), so consecutive frames carry the same field and the result stays stable.

Usage:
  python scripts/cleanup_skin_artifacts.py --video in.mp4 --out out.mp4 [--fps 48]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRATCH = os.environ.get(
    "HERMES_SCRATCH_DIR",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "cache", "scratch"),
)


def remove_dark_specks(rgb: "np.ndarray", max_size: int, strength: float) -> "np.ndarray":
    """Replace dark connected components smaller than max_size with their surround."""
    import numpy as np
    from scipy import ndimage

    lum = rgb.mean(axis=2)
    # a speck is a pixel markedly darker than its local surround
    local = ndimage.uniform_filter(lum, size=7)
    dark = (local - lum) > 18.0
    if not dark.any():
        return rgb
    labels, count = ndimage.label(dark)
    if count == 0:
        return rgb
    sizes = ndimage.sum(dark, labels, range(1, count + 1))
    small = np.zeros(count + 1, dtype=bool)
    small[1:] = sizes < max_size
    speck = small[labels]
    if not speck.any():
        return rgb
    # Fill the specks with their surround by normalised convolution: blur the
    # image with the speck pixels excluded, so the fill comes from the
    # neighbouring skin rather than from the speck itself. Much faster than a
    # median filter over the whole frame.
    keep = (~speck)[..., None].astype(np.float32)
    num = ndimage.uniform_filter(rgb * keep, size=5, axes=(0, 1))
    den = ndimage.uniform_filter(keep, size=5, axes=(0, 1))
    surround = num / np.maximum(den, 1e-6)
    out = rgb.copy()
    out[speck] = (1.0 - strength) * rgb[speck] + strength * surround[speck]
    return out


def breakup_specular(rgb: "np.ndarray", noise: "np.ndarray", threshold: float,
                      amount: float) -> "np.ndarray":
    """Add a small deterministic variation inside clipped highlight regions."""
    import numpy as np

    lum = rgb.mean(axis=2)
    mask = (lum > threshold).astype(np.float32)
    if not mask.any():
        return rgb
    # blur the mask so the breakup fades in and out of the highlight
    from scipy import ndimage
    mask = ndimage.gaussian_filter(mask, sigma=2.0)[..., None]
    out = rgb + noise[..., None] * mask * amount
    return np.clip(out, 0, 255)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=48.0)
    ap.add_argument("--speck-size", type=int, default=12,
                    help="dark components smaller than this many pixels are removed")
    ap.add_argument("--speck-strength", type=float, default=0.85)
    ap.add_argument("--specular-threshold", type=float, default=228.0)
    ap.add_argument("--specular-amount", type=float, default=2.0)
    ap.add_argument("--seed", type=int, default=20261103)
    ap.add_argument("--max-frames", type=int, default=0)
    args = ap.parse_args()

    import numpy as np
    from PIL import Image

    try:
        import scipy  # noqa: F401
    except ImportError:
        print("нужен scipy (scipy.ndimage)", flush=True)
        return 1

    work = Path(os.path.join(SCRATCH, "cleanup_work"))
    src_dir = work / "src"
    out_dir = work / "out"
    shutil.rmtree(work, ignore_errors=True)
    src_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", args.video,
                    "-fps_mode", "passthrough", str(src_dir / "f_%05d.png")],
                   check=True, timeout=1200)
    frames = sorted(src_dir.glob("f_*.png"))
    if args.max_frames:
        frames = frames[: args.max_frames]
    print(f"кадров: {len(frames)}", flush=True)

    rng = np.random.default_rng(args.seed)
    fixed_noise = None
    for i, src in enumerate(frames):
        arr = np.asarray(Image.open(src).convert("RGB")).astype(np.float32)
        if fixed_noise is None or fixed_noise.shape != arr.shape[:2]:
            # ONE fixed field for the whole clip: consecutive frames then carry
            # the same pattern, so the breakup cannot flicker.
            fixed_noise = rng.standard_normal(arr.shape[:2]).astype(np.float32)
            from scipy import ndimage
            fixed_noise = ndimage.gaussian_filter(fixed_noise, sigma=1.6)
            fixed_noise /= max(float(np.abs(fixed_noise).max()), 1e-6)
        arr = remove_dark_specks(arr, args.speck_size, args.speck_strength)
        arr = breakup_specular(arr, fixed_noise, args.specular_threshold,
                               args.specular_amount)
        Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(out_dir / src.name)
        if (i + 1) % 50 == 0 or i == 0:
            print(f"  {i + 1}/{len(frames)}", flush=True)

    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(args.fps),
                    "-i", str(out_dir / "f_%05d.png"), "-i", args.video,
                    "-map", "0:v:0", "-map", "1:a:0?",
                    "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", args.out],
                   check=True, timeout=2400)
    print(f"готово: {args.out} ({os.path.getsize(args.out) // 1024} КБ)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
