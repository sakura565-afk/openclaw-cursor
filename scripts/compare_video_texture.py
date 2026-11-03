#!/usr/bin/env python
"""Compare two clips on skin texture and temporal stability.

Every detail pass on this pipeline moves texture and flicker in the same
direction, so a single number is not enough - the pair is what matters. The
texture side uses the pore band (3-12 px) rather than a plain Laplacian,
because a Laplacian mixes pores, highlights and sharpening halo into one
number and has misled us before.

    python scripts/compare_video_texture.py A.mp4 B.mp4 [--frames 24]
    python scripts/compare_video_texture.py --ref still.png A.mp4 B.mp4
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # type: ignore
from PIL import Image  # type: ignore
from scipy import ndimage  # type: ignore

# Reuse the canonical pore metric from probe_pore_spectrum: the pore band is
# defined in pixels and the FFT implementation there is the one our earlier
# numbers were measured with. A second implementation would silently produce
# a different scale and every comparison would be meaningless.
from probe_pore_spectrum import band_energy, roi_slice  # type: ignore

# The canonical ROI geometry of the bath clip: thigh and chest.
# Format is (x0, y0, x1, y1) - the same order probe_pore_spectrum uses.
DEFAULT_ROIS = [("thigh", (0.170, 0.606, 0.469, 0.821)), ("chest", (0.717, 0.327, 0.916, 0.569))]
FACE_ROI = (0.327, 0.187, 0.568, 0.354)


def extract(video: str, out_dir: Path, frames: int) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("f_*.png"):
        old.unlink()
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", video, "-frames:v", str(frames),
         "-fps_mode", "passthrough", str(out_dir / "f_%03d.png")],
        check=True, timeout=900)
    return sorted(str(p) for p in out_dir.glob("f_*.png"))


def canonical_sizes(paths, rois=DEFAULT_ROIS):
    """ROI sizes of the first frame - the reference scale for every clip."""
    arr = np.asarray(Image.open(paths[0]).convert("RGB"))
    out = {}
    for name, roi in rois:
        y0, y1, x0, x1 = roi_slice(arr.shape[:2], roi)
        out[name] = (y1 - y0, x1 - x0)
    return out


def measure(paths, rois=DEFAULT_ROIS, face_roi=FACE_ROI, canonical=None):
    """Texture at a canonical scale plus the temporal stability metrics.

    The pore band is defined in pixels, so clips at different resolutions are
    NOT comparable unless every ROI is resampled to the same scale first. The
    temporal numbers (RGB delta, HF delta, face drift) are computed on the
    native frames, because flicker is a property of the frames as they are.
    """
    from probe_pore_spectrum import measure as probe_measure
    roi_map = {name: roi for name, roi in rois}
    spec = probe_measure(paths, roi_map, canonical=canonical)
    pores = [spec[name]["pore"] for name in spec]
    laps = [spec[name]["lap"] for name in spec]

    rgbs, hfs, face_drift = [], [], []
    prev = None
    prev_gray = None
    face_ref = None
    for p in paths:
        arr = np.asarray(Image.open(p).convert("RGB")).astype(np.float32)
        gray = arr.mean(axis=2)
        if prev is not None:
            rgbs.append(float(np.abs(arr - prev).mean()))
        if prev_gray is not None:
            l1, l2 = ndimage.laplace(prev_gray), ndimage.laplace(gray)
            hfs.append(float(np.abs(l1 - l2).mean()))
        if face_roi:
            y0, y1, x0, x1 = roi_slice(arr.shape[:2], face_roi)
            patch = arr[y0:y1, x0:x1]
            if face_ref is None:
                face_ref = patch
            else:
                face_drift.append(float(np.abs(patch - face_ref).mean()))
        prev, prev_gray = arr, gray
    return {
        "pore": float(np.mean(pores)) if pores else 0.0,
        "laplacian": float(np.mean(laps)) if laps else 0.0,
        "rgb_delta": float(np.mean(rgbs)) if rgbs else 0.0,
        "hf_delta": float(np.mean(hfs)) if hfs else 0.0,
        "face_drift": float(np.mean(face_drift)) if face_drift else 0.0,
        "face_drift_max": float(np.max(face_drift)) if face_drift else 0.0,
        "frames": len(paths),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", nargs="+", help="two or more clips to compare")
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--work", default="")
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    if len(args.clips) < 2:
        print("нужно минимум два клипа", file=sys.stderr)
        return 2

    work = Path(args.work) if args.work else Path(
        os.environ.get("HERMES_SCRATCH_DIR", str(Path.home() / "AppData/Local/hermes/cache/scratch"))
    ) / "ab_frames"
    results = {}
    canonical = None
    for clip in args.clips:
        tag = Path(clip).stem
        frames = extract(clip, work / tag, args.frames)
        if not frames:
            print(f"нет кадров: {clip}", file=sys.stderr)
            return 2
        if canonical is None:
            # The first clip defines the physical scale every other clip is
            # resampled to, so a 736-wide and a 1072-wide render are compared
            # on the same features.
            canonical = canonical_sizes(frames)
        results[tag] = measure(frames, canonical=canonical)
        r = results[tag]
        print(f"{tag:22s} поры {r['pore']:.4e} | лапласиан {r['laplacian']:6.3f} | "
              f"RGB-дельта {r['rgb_delta']:.4f} | HF-дельта {r['hf_delta']:.4f} | "
              f"лицо {r['face_drift']:.3f}/{r['face_drift_max']:.3f} | кадров {r['frames']}")

    tags = list(results)
    base = results[tags[0]]
    print(f"\nотносительно {tags[0]}:")
    for tag in tags[1:]:
        r = results[tag]
        print(f"  {tag:22s} поры {(r['pore']/base['pore']-1)*100:+6.1f}% | "
              f"RGB {(r['rgb_delta']/base['rgb_delta']-1)*100:+6.1f}% | "
              f"HF {(r['hf_delta']/base['hf_delta']-1)*100:+6.1f}% | "
              f"лицо {(r['face_drift']/base['face_drift']-1)*100:+6.1f}%")
    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print("json:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
