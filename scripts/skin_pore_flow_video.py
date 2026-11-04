#!/usr/bin/env python
"""Apply the flow-warped pore field to a video, on the CPU.

This is the combination the measurements point at:

- BCNodes' pore FIELD is good - two band-passed octaves plus sparse darker
  pits - but it draws a DIFFERENT random field per frame from one seed, so the
  pores crawl by construction. That is the source of the flicker we measured
  next to the texture gain.
- Our own injector has the right STABILITY - one field, warped by optical
  flow so it sticks to the subject, with a temporal low-pass on the injected
  amount - but a plainer field shape.

This driver puts the good shape into the stable mechanism: one field for the
whole clip, warped per frame by Farneback optical flow, so the pores follow
the skin instead of swimming, and the same pattern is present in every frame
of a given point on the body.

Usage:
    python scripts/skin_pore_flow_video.py --video in.mp4 --out out.mp4 \
        --strength 0.35 --two-octave
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from skin_detail_inject import inject_skin_detail  # type: ignore


def read_frames(path: Path):
    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, bgr = cap.read()
        if not ok:
            break
        frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0)
    cap.release()
    return frames


def farneback_flow(frames, winsize=21, levels=3, iterations=3, poly_n=5, poly_sigma=1.1):
    """Flow from frame i to frame i-1, one entry per frame.

    The injector indexes flow[i] for frame i, so frame 0 gets a zero flow
    (there is no previous frame to move from).
    """
    grays = [cv2.cvtColor((np.clip(f, 0.0, 1.0) * 255.0).astype(np.uint8),
                          cv2.COLOR_RGB2GRAY) for f in frames]
    zero = np.zeros(frames[0].shape[:2], dtype=np.float32)
    flows = [(zero, zero)]
    for i in range(1, len(grays)):
        fl = cv2.calcOpticalFlowFarneback(
            grays[i - 1], grays[i], None,
            pyr_scale=0.5, levels=levels, winsize=winsize, iterations=iterations,
            poly_n=poly_n, poly_sigma=poly_sigma, flags=0)
        flows.append((fl[..., 0].astype(np.float32), fl[..., 1].astype(np.float32)))
    return flows


def warm_skin_mask(frames, smooth_sigma=2.0, tight=False):
    """Skin prior that also works on wet, dark, warm-lit skin.

    The YCrCb prior in skin_detail_inject.auto_skin_mask returns an empty mask
    on the bath clip (wet skin sits outside the classic Cr/Cb box), which
    silently turned the whole detail pass into a no-op - every strength gave
    the identical file.

    This one works in HSV and keeps the R>G>B relation, while excluding foam
    (very bright and desaturated) and deep shadow.
    """
    masks = []
    for f in frames:
        r, g, b = f[..., 0], f[..., 1], f[..., 2]
        # the frames are float32 in [0,1] - cvtColor on those treats the values
        # as if they were already 0..255, which collapses saturation and value
        # to near zero and leaves every mask empty
        hsv = cv2.cvtColor((np.clip(f, 0.0, 1.0) * 255.0).astype(np.uint8), cv2.COLOR_RGB2HSV)
        h = hsv[..., 0].astype(np.float32) * 2.0          # 0..360
        s = hsv[..., 1].astype(np.float32) / 255.0        # 0..1
        v = hsv[..., 2].astype(np.float32) / 255.0        # 0..1
        warm_hue = (h <= 75.0) | (h >= 160.0)
        m = (warm_hue & (s > 0.10) & (v > 0.12) & (v < 0.97)
             & (r > g * 1.01) & (g > b * 0.95)).astype(np.float32)
        m = cv2.GaussianBlur(m, (0, 0), smooth_sigma)
        m = np.clip(m * 1.6, 0.0, 1.0)
        if tight:
            # Keep only the body: drop the foam and the background. Skin is
            # more saturated than the lit foam, so raise the saturation floor,
            # then keep the largest connected blob.
            m = (m * (s > 0.28)).astype(np.float32)
            m = cv2.GaussianBlur(m, (0, 0), smooth_sigma)
            binm = (m > 0.35).astype(np.uint8)
            n, lab, stats, _ = cv2.connectedComponentsWithStats(binm, 8)
            if n > 1:
                biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
                if stats[biggest, cv2.CC_STAT_AREA] > 0.02 * m.size:
                    m = ((lab == biggest) | (m > 0.85)).astype(np.float32)
                    m = cv2.GaussianBlur(m, (0, 0), smooth_sigma)
        masks.append(m)
    return masks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--strength", type=float, default=0.35)
    ap.add_argument("--detail-sigma", type=float, default=1.6)
    ap.add_argument("--pore-sigma", type=float, default=0.55)
    ap.add_argument("--temporal-sigma", type=float, default=2.0)
    ap.add_argument("--detail-floor", type=float, default=0.35)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--single-octave", action="store_true")
    ap.add_argument("--no-flow", action="store_true")
    ap.add_argument("--tight-mask", action="store_true")
    args = ap.parse_args()

    src = Path(args.video)
    if not src.exists():
        print("video not found:", src, file=sys.stderr)
        return 2

    frames = read_frames(src)
    if args.max_frames:
        frames = frames[: args.max_frames]
    print(f"кадров: {len(frames)}", flush=True)

    flow = None
    if not args.no_flow:
        flow = farneback_flow(frames)
        print("flow готов ✓", flush=True)

    out_frames = inject_skin_detail(
        frames,
        skin_mask=warm_skin_mask(frames, tight=args.tight_mask),
        strength=args.strength,
        detail_sigma=args.detail_sigma,
        pore_sigma=args.pore_sigma,
        flow=flow,
        seed=args.seed,
        temporal_sigma=args.temporal_sigma,
        detail_floor=args.detail_floor,
        two_octave=not args.single_octave,
    )

    work = Path(os.environ.get("HERMES_SCRATCH_DIR",
                               str(Path.home() / "AppData/Local/hermes/cache/scratch")))
    out_dir = work / "pore_flow_frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("f_*.png"):
        old.unlink()
    for i, f in enumerate(out_frames):
        bgr = cv2.cvtColor((np.clip(f, 0.0, 1.0) * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
        cv2.imwrite(str(out_dir / f"f_{i + 1:05d}.png"), bgr)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-framerate", "24", "-start_number", "1",
         "-i", str(out_dir / "f_%05d.png"), "-i", str(src),
         "-map", "0:v:0", "-map", "1:a:0?",
         "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)],
        check=True, timeout=3600)
    print("saved:", out, out.stat().st_size, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
