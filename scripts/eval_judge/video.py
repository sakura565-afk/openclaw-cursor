"""Video probing and evenly spread frame extraction.

The frames of every clip are sampled at the SAME timestamps, so a flicker
comparison is between the same moments of the take and not between "whatever
frame number N happened to be".
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

# A frame rate difference below this is treated as the same rate.
FPS_EPSILON = 0.5


class VideoError(RuntimeError):
    """Raised when a clip cannot be probed or decoded."""


def probe(path: Path | str) -> dict:
    """Return width/height/fps/frames/duration of the first video stream."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries",
        "stream=width,height,nb_read_frames,r_frame_rate,avg_frame_rate,duration",
        "-of", "json", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise VideoError(f"ffprobe failed for {path}: {proc.stderr[:300]}")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:  # pragma: no cover - ffprobe contract
        raise VideoError(f"ffprobe returned invalid JSON for {path}") from exc
    streams = data.get("streams") or []
    if not streams:
        raise VideoError(f"no video stream in {path}")
    stream = streams[0]
    rate = stream.get("r_frame_rate") or stream.get("avg_frame_rate") or "24/1"
    try:
        num, den = rate.split("/")
        fps = float(num) / max(1.0, float(den))
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    frames = 0
    try:
        frames = int(stream.get("nb_read_frames") or 0)
    except (TypeError, ValueError):
        frames = 0
    duration = 0.0
    try:
        duration = float(stream.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "frames": frames,
        "fps": round(fps, 6),
        "duration": round(duration, 6),
    }


def uniform_timestamps(duration: float, count: int) -> list[float]:
    """Evenly spread ``count`` timestamps over the clip.

    The sample points sit in the middle of each equal slice, so the first
    frame (often a fade-in or a slate) and the last one are not over-weighted.
    """
    if count <= 0:
        return []
    if duration <= 0:
        return [0.0 for _ in range(count)]
    return [duration * (index + 0.5) / count for index in range(count)]


def extract_frames(
    video: Path | str,
    timestamps: list[float],
    out_dir: Path,
    *,
    prefix: str = "f",
) -> list[Path]:
    """Decode one PNG per timestamp into ``out_dir``.

    The seek is accurate (ffmpeg decodes up to the requested timestamp), so
    the samples really are at the requested moments of the take.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, timestamp in enumerate(timestamps):
        target = out_dir / f"{prefix}{index:03d}.png"
        cmd = [
            "ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, float(timestamp)):.6f}",
            "-i", str(video), "-frames:v", "1", "-f", "image2", str(target),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0 or not target.exists():
            raise VideoError(
                f"ffmpeg failed to decode {video} at {timestamp:.3f}s: {proc.stderr[:300]}"
            )
        paths.append(target)
    return paths


def load_rgb(path: Path | str) -> np.ndarray:
    """Load a frame as uint8 RGB, whatever the on-disk format."""
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def frame_correlation(a: np.ndarray, b: np.ndarray, size: int = 64) -> float:
    """Pearson correlation of two frames at low resolution.

    Used as a "is this even the same take?" probe: two renders of the same
    motion correlate highly, two different takes do not.

    A frame with no variance at all (a flat colour) has an undefined
    correlation, so the degenerate case is handled explicitly: two identical
    flat frames are the SAME content and score 1.0, and two different flat
    frames score 0.0. Returning 0.0 for identical flat frames would make a
    static clip look like a different take.
    """
    left_raw = _small_gray(a, size).astype(np.float64).ravel()
    right_raw = _small_gray(b, size).astype(np.float64).ravel()
    left = left_raw - left_raw.mean()
    right = right_raw - right_raw.mean()
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator <= 1e-9:
        # At least one frame is flat. Equal content means the same frame.
        return 1.0 if np.allclose(left_raw, right_raw) else 0.0
    return float(np.dot(left, right) / denominator)


def _small_gray(rgb: np.ndarray, size: int) -> np.ndarray:
    array = np.asarray(rgb, dtype=np.float32)
    if array.ndim == 3:
        array = array[..., :3].mean(axis=2)
    height, width = array.shape[:2]
    ys = np.linspace(0, height - 1, size).astype(np.int32)
    xs = np.linspace(0, width - 1, size).astype(np.int32)
    return array[np.ix_(ys, xs)]
