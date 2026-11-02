#!/usr/bin/env python3
"""Measure temporal motion and frozen regions in a video clip."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
from PIL import Image

DEFAULT_FREEZE_THRESHOLD = 1.0
DEFAULT_FREEZE_WARN = 0.50
DEFAULT_FREEZE_FAIL = 0.80


def probe_video(path: str | Path) -> dict[str, float | int]:
    """Read dimensions and average frame rate with ffprobe."""
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe is not available on PATH")
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate,r_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()}")
    streams = json.loads(proc.stdout).get("streams", [])
    if not streams:
        raise ValueError("video stream not found")
    stream = streams[0]
    rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
    numerator, denominator = (float(part) for part in rate.split("/", 1))
    fps = numerator / denominator if denominator else 0.0
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": float(fps),
    }


def extract_frames(path: str | Path, *, timeout: int = 300) -> tuple[list[np.ndarray], dict[str, float | int]]:
    """Decode the first video stream to grayscale frames using ffmpeg rawvideo output."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not available on PATH")
    info = probe_video(path)
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extraction failed: {proc.stderr.decode(errors='replace').strip()}")
    width, height = int(info["width"]), int(info["height"])
    frame_size = width * height
    if not proc.stdout or len(proc.stdout) % frame_size:
        raise RuntimeError("ffmpeg returned incomplete grayscale frame data")
    buffer = np.frombuffer(proc.stdout, dtype=np.uint8)
    frames = list(buffer.reshape(-1, height, width))
    return frames, info


def analyze_video(
    path: str | Path,
    *,
    roi: tuple[int, int, int, int] | None = None,
    mask: str | Path | None = None,
    threshold: float = DEFAULT_FREEZE_THRESHOLD,
    freeze_warn: float = DEFAULT_FREEZE_WARN,
    freeze_fail: float = DEFAULT_FREEZE_FAIL,
) -> dict[str, object]:
    """Decode a clip and return its temporal QC report."""
    frames, info = extract_frames(path)
    mask_array = None
    if mask is not None:
        with Image.open(mask) as mask_image:
            mask_array = np.asarray(mask_image.convert("L")) > 127
    report = analyze_frames(
        frames,
        roi=roi,
        mask=mask_array,
        threshold=threshold,
        freeze_warn=freeze_warn,
        freeze_fail=freeze_fail,
    )
    report.update({"source": str(path), "mask": str(mask) if mask is not None else None, **info})
    return report


def _as_gray(frame: np.ndarray) -> np.ndarray:
    array = np.asarray(frame)
    if array.ndim == 2:
        return array if array.dtype == np.uint8 else array.astype(np.float32)
    if array.ndim == 3 and array.shape[2] >= 3:
        rgb = array[..., :3].astype(np.float32)
        return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    raise ValueError("frames must be HxW grayscale or HxWx3 RGB arrays")


def _verdict(
    freeze_score: float,
    motion_energy: float,
    threshold: float,
    freeze_warn: float,
    freeze_fail: float,
) -> str:
    if freeze_score >= freeze_fail or motion_energy < threshold:
        return "FAIL"
    if freeze_score >= freeze_warn:
        return "WARN"
    return "PASS"


def _block_motion(
    previous: np.ndarray,
    current: np.ndarray,
    *,
    block_size: int,
    search_radius: int,
    selection: np.ndarray | None,
) -> tuple[list[tuple[float, float]], tuple[float, float]]:
    """Match textured blocks on a regular grid and return their displacement vectors."""
    height, width = previous.shape
    vectors: list[tuple[float, float]] = []
    for y in range(0, height - block_size + 1, block_size):
        for x in range(0, width - block_size + 1, block_size):
            template = previous[y : y + block_size, x : x + block_size]
            if selection is not None and float(np.mean(selection[y : y + block_size, x : x + block_size])) < 0.5:
                continue
            if float(np.std(template)) < 1.0:
                continue
            search_x = max(0, x - search_radius)
            search_y = max(0, y - search_radius)
            search_right = min(width, x + block_size + search_radius)
            search_bottom = min(height, y + block_size + search_radius)
            search = current[search_y:search_bottom, search_x:search_right]
            result = cv2.matchTemplate(search, template, cv2.TM_SQDIFF)
            _, _, best, _ = cv2.minMaxLoc(result)
            dx = search_x + best[0] - x
            dy = search_y + best[1] - y
            vectors.append((float(dx), float(dy)))
    if not vectors:
        return vectors, (0.0, 0.0)
    mean_vector = (float(np.mean([v[0] for v in vectors])), float(np.mean([v[1] for v in vectors])))
    return vectors, mean_vector


def analyze_frames(
    frames: Sequence[np.ndarray],
    *,
    roi: tuple[int, int, int, int] | None = None,
    mask: np.ndarray | None = None,
    threshold: float = DEFAULT_FREEZE_THRESHOLD,
    freeze_warn: float = DEFAULT_FREEZE_WARN,
    freeze_fail: float = DEFAULT_FREEZE_FAIL,
    block_size: int = 16,
    search_radius: int = 4,
) -> dict[str, object]:
    """Return aggregate motion metrics for consecutive grayscale/RGB frames."""
    if len(frames) == 0:
        raise ValueError("at least one frame is required")
    if threshold <= 0:
        raise ValueError("threshold must be positive")
    if not 0.0 <= freeze_warn <= freeze_fail <= 1.0:
        raise ValueError("freeze thresholds must satisfy 0 <= warn <= fail <= 1")
    if block_size <= 0 or search_radius < 0:
        raise ValueError("block_size must be positive and search_radius non-negative")
    gray = [_as_gray(frame) for frame in frames]
    if any(frame.shape != gray[0].shape for frame in gray[1:]):
        raise ValueError("all frames must have the same dimensions")

    selection: np.ndarray | None = None
    roi_dict: dict[str, int] | None = None
    frame_height, frame_width = gray[0].shape
    if roi is not None:
        x, y, width, height = (int(value) for value in roi)
        if (
            min(width, height) <= 0
            or x < 0
            or y < 0
            or x + width > frame_width
            or y + height > frame_height
        ):
            raise ValueError("roi must be a positive x,y,w,h box inside the frame")
        selection = np.zeros((frame_height, frame_width), dtype=bool)
        selection[y : y + height, x : x + width] = True
        roi_dict = {"x": x, "y": y, "w": width, "h": height}
    if mask is not None:
        mask_array = np.asarray(mask, dtype=bool)
        if mask_array.shape != gray[0].shape:
            raise ValueError("mask dimensions must match video frames")
        if not np.any(mask_array):
            raise ValueError("mask must select at least one pixel")
        selection = mask_array if selection is None else selection & mask_array
        if not np.any(selection):
            raise ValueError("ROI and mask do not overlap")

    global_pair_energy: list[float] = []
    selected_pair_energy: list[float] = []
    drift: list[float] = []
    block_vectors: list[tuple[float, float]] = []
    pair_vectors: list[tuple[float, float]] = []
    first = gray[0]
    for previous, current in zip(gray, gray[1:]):
        diff = np.abs(np.subtract(current, previous, dtype=np.float32))
        global_pair_energy.append(float(np.mean(diff)))
        if selection is not None:
            selected_pair_energy.append(float(np.mean(diff[selection])))
        drift.append(float(np.mean(np.abs(np.subtract(current, first, dtype=np.float32)))))
        vectors, mean_vector = _block_motion(
            previous,
            current,
            block_size=block_size,
            search_radius=search_radius,
            selection=selection,
        )
        block_vectors.extend(vectors)
        pair_vectors.append(mean_vector)

    pair_energy = selected_pair_energy if selection is not None else global_pair_energy
    energy = float(np.mean(pair_energy)) if pair_energy else 0.0
    global_energy = float(np.mean(global_pair_energy)) if global_pair_energy else 0.0
    freeze_score = float(np.mean(np.asarray(pair_energy) < threshold)) if pair_energy else 1.0
    temporal_std = float(np.std(pair_energy)) if pair_energy else 0.0
    if block_vectors:
        motion_vector = [
            float(np.mean([vector[0] for vector in block_vectors])),
            float(np.mean([vector[1] for vector in block_vectors])),
        ]
    else:
        motion_vector = [0.0, 0.0]
    block_magnitudes = [float(np.hypot(*vector)) for vector in block_vectors]

    return {
        "frame_count": len(gray),
        "pair_count": len(pair_energy),
        "width": int(gray[0].shape[1]),
        "height": int(gray[0].shape[0]),
        "global_energy": global_energy,
        "roi": roi_dict,
        "roi_energy": energy if roi is not None else None,
        "mask_energy": energy if mask is not None and roi is None else None,
        "motion_energy": energy,
        "freeze_score": freeze_score,
        "temporal_std": temporal_std,
        "drift_vs_first": float(np.mean(drift)) if drift else 0.0,
        "motion_vector": motion_vector,
        "motion_magnitude": float(np.hypot(*motion_vector)),
        "mean_block_magnitude": float(np.mean(block_magnitudes)) if block_magnitudes else 0.0,
        "thresholds": {
            "pair_energy": float(threshold),
            "freeze_warn": float(freeze_warn),
            "freeze_fail": float(freeze_fail),
        },
        "per_pair": {
            "global_energy": global_pair_energy,
            "motion_energy": pair_energy,
            "motion_vector": [[float(v[0]), float(v[1])] for v in pair_vectors],
            "drift_vs_first": drift,
        },
        "verdict": _verdict(freeze_score, energy, threshold, freeze_warn, freeze_fail),
    }


def _parse_roi(value: str) -> tuple[int, int, int, int]:
    try:
        parts = tuple(int(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ROI must contain four integers: x,y,w,h") from exc
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("ROI must contain four integers: x,y,w,h")
    return parts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clip", type=Path, help="Video clip to analyze")
    parser.add_argument("--roi", type=_parse_roi, help="Motion measurement box: x,y,w,h")
    parser.add_argument("--mask", type=Path, help="Optional grayscale image; pixels above 127 are measured")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_FREEZE_THRESHOLD,
        help="Per-pixel grayscale difference below which a frame pair is frozen",
    )
    parser.add_argument(
        "--freeze-warn",
        type=float,
        default=DEFAULT_FREEZE_WARN,
        help="Frozen-pair fraction that triggers WARN (default 0.50)",
    )
    parser.add_argument(
        "--freeze-fail",
        type=float,
        default=DEFAULT_FREEZE_FAIL,
        help="Frozen-pair fraction that triggers FAIL (default 0.80)",
    )
    parser.add_argument("--json", type=Path, help="Also write the full report to this JSON file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = analyze_video(
            args.clip,
            roi=args.roi,
            mask=args.mask,
            threshold=args.threshold,
            freeze_warn=args.freeze_warn,
            freeze_fail=args.freeze_fail,
        )
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"video motion QC: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
