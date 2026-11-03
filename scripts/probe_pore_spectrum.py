# -*- coding: utf-8 -*-
"""Spectral pore measurement: separate real pores from glossy highlights.

Why this exists
---------------
A plain mean |laplacian| cannot tell a pore from a specular edge - both are
high-frequency energy. Our first pass at the skin pipeline was guided by that
number, and it led us to a wrong conclusion (H3 looked like it had more skin
texture than FreeVideo when it actually had less).

The fix measures the ENERGY SPECTRUM of the skin region:
  * pore band  - spatial periods of 3-12 px
  * highlight band - spatial periods above 25 px
A real pore survives a light blur; a sharpening halo and digital noise do not.
So we also report the survival ratio after a sigma=1.2 Gaussian blur.

Usage:
  python scripts/probe_pore_spectrum.py --video a.mp4 --video b.mp4 \
      --roi 0.170,0.606,0.469,0.821 --roi 0.717,0.327,0.916,0.569
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image


def extract_frames(video: str, work: str, n: int = 12) -> list:
    out = os.path.join(work, "frames")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", video, "-frames:v", str(n),
                    "-fps_mode", "passthrough", os.path.join(out, "f_%03d.png")],
                   check=True, timeout=600)
    return sorted(os.path.join(out, f) for f in os.listdir(out) if f.endswith(".png"))


def band_energy(gray: np.ndarray, lo: float, hi: float) -> float:
    """Energy of the spatial-frequency band with periods in [lo, hi] pixels."""
    f = np.fft.fftshift(np.fft.fft2(gray))
    h, w = gray.shape
    cy, cx = h // 2, w // 2
    yy = (np.arange(h) - cy)[:, None]
    xx = (np.arange(w) - cx)[None, :]
    # period p pixels -> frequency 1/p cycles per pixel
    r = np.sqrt((yy / h) ** 2 + (xx / w) ** 2) + 1e-9
    period = 1.0 / r
    mask = (period >= lo) & (period < hi)
    return float(np.abs(f[mask]) ** 2 .__class__ if False else (np.abs(f[mask]) ** 2).sum())


def roi_slice(shape, roi):
    """Normalised (x0, y0, x1, y1) -> pixel slices."""
    h, w = shape
    x0, y0, x1, y1 = roi
    return (int(y0 * h), int(y1 * h), int(x0 * w), int(x1 * w))


def measure(paths, rois, blur_sigma=1.2, canonical=None):
    """Measure pores at a CANONICAL scale.

    The pore band is defined in PIXELS, so two clips at different resolutions
    are not comparable: a 3-12 px pore at 1072x1620 is a different physical
    feature than a 3-12 px pore at 704x1056. Every ROI is therefore resampled
    to `canonical` (default: the first frame's own ROI size) before the
    spectrum is measured, which makes numbers from different renders directly
    comparable.
    """
    from scipy import ndimage
    rows = []
    for p in paths:
        arr = np.asarray(Image.open(p).convert("RGB")).astype(np.float32)
        row = {}
        for name, roi in rois.items():
            y0, y1, x0, x1 = roi_slice(arr.shape[:2], roi)
            crop = arr[y0:y1, x0:x1]
            if canonical is not None and name in canonical:
                ch, cw = canonical[name]
                if (crop.shape[0], crop.shape[1]) != (ch, cw):
                    crop = np.asarray(Image.fromarray(crop.astype(np.uint8)).resize(
                        (cw, ch), Image.LANCZOS)).astype(np.float32)
            gray = crop.mean(axis=2)
            pore = band_energy(gray, 3.0, 12.0)
            spec = band_energy(gray, 25.0, 1e9)
            blurred = ndimage.gaussian_filter(gray, sigma=blur_sigma)
            pore_blur = band_energy(blurred, 3.0, 12.0)
            lap = float(np.abs(ndimage.laplace(gray)).mean())
            row[name] = {
                "lap": lap,
                "pore": pore,
                "spec": spec,
                "pore_ratio": pore / max(spec, 1e-9),
                "survival": pore_blur / max(pore, 1e-9),
            }
        rows.append(row)
    out = {}
    for name in rois:
        out[name] = {k: float(np.mean([r[name][k] for r in rows]))
                     for k in ("lap", "pore", "spec", "pore_ratio", "survival")}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", action="append", required=True)
    ap.add_argument("--label", action="append", default=[])
    ap.add_argument("--roi", action="append", default=[],
                    help="name=x0,y0,x1,y1 (normalised)")
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--work", default=os.path.join(
        os.environ.get("HERMES_SCRATCH_DIR",
                       os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes",
                                    "cache", "scratch")), "pore_probe"))
    ap.add_argument("--json-out", default="")
    ap.add_argument("--reference", default="",
                    help="reference IMAGE that defines the canonical ROI geometry")
    args = ap.parse_args()

    rois = {}
    for spec in args.roi:
        name, vals = spec.split("=", 1)
        rois[name] = tuple(float(x) for x in vals.split(","))
    if not rois:
        rois = {"thigh": (0.170, 0.606, 0.469, 0.821),
                "chest": (0.717, 0.327, 0.916, 0.569)}

    labels = args.label or [os.path.basename(v) for v in args.video]
    # ONE canonical ROI geometry for the whole run, taken from the reference
    # image. Measuring every clip in the same pixel geometry is what makes
    # numbers from different resolutions comparable.
    canonical_rois = None
    if args.reference:
        ref = np.asarray(Image.open(args.reference).convert("RGB")).astype(np.float32)
        canonical_rois = {}
        for name, roi in rois.items():
            y0, y1, x0, x1 = roi_slice(ref.shape[:2], roi)
            canonical_rois[name] = (y1 - y0, x1 - x0)
        print(f"canonical ROI (из {os.path.basename(args.reference)}): "
              + ", ".join(f"{k}={v[1]}x{v[0]}" for k, v in canonical_rois.items()))
    results = {}
    for label, video in zip(labels, args.video):
        if not os.path.exists(video):
            print(f"нет файла: {video}", file=sys.stderr)
            continue
        paths = extract_frames(video, args.work, args.frames)
        if not paths:
            continue
        results[label] = measure(paths, rois, canonical=canonical_rois)
        print(f"\n=== {label}  ({len(paths)} кадров) ===")
        for name, m in results[label].items():
            print(f"  {name:8s} лапласиан {m['lap']:7.3f} | поры {m['pore']:.3e} | "
                  f"блики {m['spec']:.3e} | поры/блики {m['pore_ratio']:.3f} | "
                  f"выживание пор после блюра {m['survival']*100:.1f}%")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=2)
        print(f"\nJSON: {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
