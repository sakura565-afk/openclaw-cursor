#!/usr/bin/env python
"""Skin SSS and specular micro-bloom for video, on the CPU.

Two pixel-space passes that fix the "plastic" look rather than adding pores:

- **Subsurface diffusion** blurs ONLY the red channel and blends it back.
  Green and blue - where the pore detail lives - keep every pixel of their
  sharpness, so the skin gains a living tone without losing texture. This is
  the mechanism behind "why does skin look plastic" and it is the one thing
  our measured candidates never addressed.
- **Specular micro-bloom** extracts the extreme highlights with a soft knee
  and diffuses them in two layers, which is what puts the moist
  micro-speculars back on pores and sweat.

Both are from the Post Processing PRO pack (Guillaume-127), reimplemented
here as a standalone CPU script so they can be applied to a whole clip
without a ComfyUI restart, and so the effect can be measured on its own.

The pair is documented as matched: SSS addresses why skin looks plastic, and
micro-bloom addresses the missing micro-speculars. Used alone, neither
completes the other.

Usage:
    python scripts/skin_sss_bloom.py --video in.mp4 --out out.mp4 \
        --sss-radius 15 --sss-strength 0.8 --bloom-strength 1.5
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np


def sss_frame(img: np.ndarray, radius: int, strength: float) -> np.ndarray:
    """Blur only the red channel and blend it back."""
    ksize = int(radius) | 1
    if ksize <= 1:
        return img
    sigma = ksize / 3.0
    r, g, b = img[:, :, 0], img[:, :, 1], img[:, :, 2]
    scattered = cv2.GaussianBlur(r, (ksize, ksize), sigmaX=sigma, sigmaY=sigma)
    out = img.copy()
    out[:, :, 0] = r * (1.0 - strength) + scattered * strength
    return out


def bloom_frame(img: np.ndarray, threshold: float, softness: float,
                strength: float, radius: int, spread: float) -> np.ndarray:
    """Soft-knee highlight extraction with a two-layer diffusion."""
    ksize = int(radius) | 1
    knee = softness * threshold + 1e-5
    rq = np.clip(img - threshold + knee, 0.0, 2.0 * knee)
    rq = (rq ** 2) / (4.0 * knee)
    specular = np.where(img > threshold + knee, img - threshold, rq)
    specular = np.clip(specular * 2.0, 0.0, 5.0)
    if ksize > 1:
        sigma1 = ksize / 2.0
        bloom1 = cv2.GaussianBlur(specular, (ksize, ksize), sigmaX=sigma1, sigmaY=sigma1)
        r2 = int(ksize * spread) | 1
        sigma2 = r2 / 2.0
        bloom2 = cv2.GaussianBlur(specular, (r2, r2), sigmaX=sigma2, sigmaY=sigma2)
        micro = 0.7 * bloom1 + 0.3 * bloom2
    else:
        micro = specular
    return np.clip(img + micro * strength, 0.0, 1.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sss-radius", type=int, default=15,
                    help="red channel scatter radius (the pack says keep it in 10-25)")
    ap.add_argument("--sss-strength", type=float, default=0.8,
                    help="0.8 is the pack default; back off to 0.5 if the blush goes")
    ap.add_argument("--bloom-strength", type=float, default=1.5)
    ap.add_argument("--bloom-threshold", type=float, default=0.80)
    ap.add_argument("--bloom-softness", type=float, default=0.50)
    ap.add_argument("--bloom-radius", type=int, default=5)
    ap.add_argument("--bloom-spread", type=float, default=2.0)
    ap.add_argument("--no-bloom", action="store_true")
    ap.add_argument("--no-sss", action="store_true")
    args = ap.parse_args()

    src = Path(args.video)
    if not src.exists():
        print("video not found:", src, file=sys.stderr)
        return 2

    work = Path(os.environ.get("HERMES_SCRATCH_DIR",
                               str(Path.home() / "AppData/Local/hermes/cache/scratch")))
    frames_dir = work / "sss_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("f_*.png"):
        old.unlink()

    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-fps_mode", "passthrough",
         str(frames_dir / "f_%05d.png")],
        check=True, timeout=1800)

    frames = sorted(frames_dir.glob("f_*.png"))
    print(f"кадров: {len(frames)}", flush=True)
    out_dir = work / "sss_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("f_*.png"):
        old.unlink()

    for i, fp in enumerate(frames):
        img = cv2.imread(str(fp), cv2.IMREAD_COLOR)
        if img is None:
            continue
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        if not args.no_sss:
            rgb = sss_frame(rgb, args.sss_radius, args.sss_strength)
        if not args.no_bloom:
            rgb = bloom_frame(rgb, args.bloom_threshold, args.bloom_softness,
                              args.bloom_strength, args.bloom_radius, args.bloom_spread)
        out = cv2.cvtColor((np.clip(rgb, 0.0, 1.0) * 255).astype(np.uint8),
                           cv2.COLOR_RGB2BGR)
        cv2.imwrite(str(out_dir / f"f_{i + 1:05d}.png"), out)
        if (i + 1) % 50 == 0 or i == 0:
            print(f"  {i + 1}/{len(frames)}", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-framerate", "24", "-start_number", "1",
           "-i", str(out_dir / "f_%05d.png"), "-i", str(src),
           "-map", "0:v:0", "-map", "1:a:0?",
           "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
    # The frame count is pinned: with -shortest the length came from the
    # audio stream, and an AAC stream is never exactly as long as the video -
    # the 7.29 s clip came out one frame short and the picture finished
    # before the music did. The audio is copied, not re-encoded.
           "-c:a", "copy", "-frames:v", str(len(frames)), str(out)]
    subprocess.run(cmd, check=True, timeout=3600)
    print("saved:", out, out.stat().st_size, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
