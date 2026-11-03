# -*- coding: utf-8 -*-
"""Measure skin micro-texture (mean |laplacian|) and temporal delta on a clip.

Metric follows the established probe pattern (scripts/probe_skin_detail.py,
scripts/probe_skin_temporal.py): mean |laplacian| over a skin ROI and over the
whole frame, plus the frame-to-frame delta and the high-frequency temporal delta
(delta of the laplacian field = flicker of the detail itself).

ROIs are given in NORMALISED coordinates (fractions of width/height) so that the
same anatomical region is measured whatever the render resolution is (FreeVideo
renders 736x1120, H3 renders 704x1056, the still is 704x1072).

ROI boxes were picked on the approved still FINAL_bathe.png (704x1072):
  thigh  (0.170, 0.606, 0.469, 0.821) - 100 % bare skin, visible pores
  chest  (0.717, 0.327, 0.916, 0.569) - 94 % bare skin (shoulder/upper arm)
  face   (0.327, 0.187, 0.568, 0.354) - 59 % skin (contains hair/eyes)

Frames are extracted with ffmpeg so the measurement runs on the exact pixels the
video file carries (compression included).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

# name -> normalised (x1, y1, x2, y2)
DEFAULT_ROIS: dict[str, tuple[float, float, float, float]] = {
    "thigh": (0.170, 0.606, 0.469, 0.821),
    "chest": (0.717, 0.327, 0.916, 0.569),
    "face": (0.327, 0.187, 0.568, 0.354),
}


def laplacian(gray: np.ndarray) -> np.ndarray:
    return (
        -4 * gray[1:-1, 1:-1]
        + gray[:-2, 1:-1] + gray[2:, 1:-1]
        + gray[1:-1, :-2] + gray[1:-1, 2:]
    )


def _skin_mask(rgb: np.ndarray) -> np.ndarray:
    """Classic YCbCr skin mask (same rule as probe_skin_detail.crop_metric)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return (cb > 77) & (cb < 127) & (cr > 133) & (cr < 173) & (r > g * 1.02)


def roi_box(size: tuple[int, int], norm: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    width, height = size
    x1 = int(round(norm[0] * width))
    y1 = int(round(norm[1] * height))
    x2 = int(round(norm[2] * width))
    y2 = int(round(norm[3] * height))
    return (x1, y1, x2, y2)


def roi_metrics(rgb: np.ndarray) -> tuple[float, dict[str, float]]:
    """Return (whole-frame mean |laplacian|, {roi name: mean |laplacian| on skin})."""
    y = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    values = np.abs(laplacian(y))
    whole = float(values.mean())
    out: dict[str, float] = {}
    size = (rgb.shape[1], rgb.shape[0])
    skin = _skin_mask(rgb)
    for name, norm in DEFAULT_ROIS.items():
        x1, y1, x2, y2 = roi_box(size, norm)
        sub_values = values[y1:y2 - 1, x1:x2 - 1]
        sub_skin = skin[y1:y2 - 1, x1:x2 - 1]
        selected = sub_values[sub_skin]
        if selected.size < 200:
            selected = sub_values.ravel()
        out[name] = float(selected.mean())
    return whole, out


def extract_frames(video: Path, count: int, start: int, step: int, work: Path) -> list[Path]:
    work.mkdir(parents=True, exist_ok=True)
    end = start + count * step
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-i", str(video),
        "-vf", f"select='between(n\\,{start}\\,{end})'",
        "-fps_mode", "passthrough", str(work / "f_%05d.png"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[:400]}")
    files = sorted(work.glob("f_*.png"))
    if not files:
        raise RuntimeError("ffmpeg extracted no frames")
    return files


def measure_video(video: Path, count: int, start: int, step: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="phv_") as tmp:
        work = Path(tmp)
        files = extract_frames(video, count, start, step, work)
        wholes: list[float] = []
        rois: dict[str, list[float]] = {name: [] for name in DEFAULT_ROIS}
        rgb_frames: list[np.ndarray] = []
        lap_frames: list[np.ndarray] = []
        for index, path in enumerate(files):
            if index % step:
                continue
            rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
            whole, per_roi = roi_metrics(rgb)
            wholes.append(whole)
            for name, value in per_roi.items():
                rois[name].append(value)
            y = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
            rgb_frames.append(rgb)
            lap_frames.append(laplacian(y))
        deltas = [float(np.abs(rgb_frames[i + 1] - rgb_frames[i]).mean())
                  for i in range(len(rgb_frames) - 1)]
        hf_deltas = [float(np.abs(lap_frames[i + 1] - lap_frames[i]).mean())
                     for i in range(len(lap_frames) - 1)]
    result = {
        "video": str(video),
        "sampled_frames": len(wholes),
        "whole_microtexture": round(float(np.mean(wholes)), 4),
        "whole_microtexture_std": round(float(np.std(wholes)), 4),
        "temporal_delta": round(float(np.mean(deltas)), 4) if deltas else None,
        "hf_temporal_delta": round(float(np.mean(hf_deltas)), 4) if hf_deltas else None,
    }
    for name, values in rois.items():
        result[f"roi_{name}"] = round(float(np.mean(values)), 4)
    # the headline "skin" number: the mean over the two clean body ROIs
    result["roi_skin_mean"] = round(float(np.mean([result["roi_thigh"], result["roi_chest"]])), 4)
    return result


def measure_still(image: Path) -> dict:
    rgb = np.asarray(Image.open(image).convert("RGB"), dtype=np.float32)
    whole, per_roi = roi_metrics(rgb)
    result = {
        "video": str(image),
        "sampled_frames": 1,
        "whole_microtexture": round(whole, 4),
        "whole_microtexture_std": 0.0,
        "temporal_delta": None,
        "hf_temporal_delta": None,
    }
    for name, value in per_roi.items():
        result[f"roi_{name}"] = round(value, 4)
    result["roi_skin_mean"] = round(float(np.mean([per_roi["thigh"], per_roi["chest"]])), 4)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--count", type=int, default=24)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--still", action="store_true", help="input is a still image")
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()
    if args.still:
        result = measure_still(args.video)
    else:
        result = measure_video(args.video, args.count, args.start, args.step)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
