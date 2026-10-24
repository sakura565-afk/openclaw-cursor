#!/usr/bin/env python3
"""Detect horizontal stripe (banding) artifacts in video frames or images."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


@dataclass
class FramePeriodic:
    """Per-frame periodic horizontal stripe pattern metrics."""

    detected: bool = False
    lag: int = 0
    best_corr: float = 0.0
    median_corr: float = 0.0
    amplitude: float = 0.0
    channel: str = ""


@dataclass
class FrameBands:
    """Per-frame horizontal band detection result."""

    bands: list[tuple[int, int, int]] = field(default_factory=list)
    band_count: int = 0
    total_band_height: int = 0
    green_dominance: float = 0.0
    periodic: FramePeriodic = field(default_factory=FramePeriodic)


@dataclass
class BandReport:
    """Aggregated banding analysis across frames."""

    band_count: int = 0
    band_height_px: float = 0.0
    affected_frame_ratio: float = 0.0
    score: float = 0.0
    verdict: str = "clean"
    bands: list[dict[str, Any]] = field(default_factory=list)
    frames_analyzed: int = 0
    green_dominance: float = 0.0
    periodic: bool = False
    periodic_lag: float = 0.0
    periodic_best_corr: float = 0.0
    periodic_median_corr: float = 0.0
    periodic_amplitude: float = 0.0
    periodic_channel: str = ""
    periodic_frame_ratio: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(
            f"{name} is not available on PATH; install ffmpeg/ffprobe to analyze video"
        )


def _median_filter_1d(values: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return values.copy()
    window = int(window)
    if window % 2 == 0:
        window += 1
    half = window // 2
    padded = np.pad(values, (half, half), mode="edge")
    out = np.empty_like(values)
    for i in range(len(values)):
        out[i] = np.median(padded[i : i + window])
    return out


def _mad(values: np.ndarray) -> float:
    med = float(np.median(values))
    return float(np.median(np.abs(values - med)))


def _rows_exceeding_threshold(
    profile: np.ndarray,
    window: int,
    k: float,
    mad_floor: float = 1.0,
) -> np.ndarray:
    """Return boolean mask of rows whose profile deviates robustly from smoothed local."""
    smoothed = _median_filter_1d(profile, window)
    residual = np.abs(profile - smoothed)
    scale = max(_mad(residual), mad_floor)
    threshold = float(np.median(residual)) + k * scale
    return residual > threshold


def _wide_window(height: int, smooth_window: int) -> int:
    wide = min(max(smooth_window * 7, 31), height if height % 2 else height - 1)
    if wide % 2 == 0:
        wide -= 1
    return max(7, wide)


def _wide_profile_anomaly(
    profile: np.ndarray,
    wide: int,
    k: float,
    mad_floor: float = 1.0,
) -> np.ndarray:
    """Rows whose profile differs from a wide vertical baseline (full band extent)."""
    baseline = _median_filter_1d(profile, wide)
    residual = np.abs(profile - baseline)
    scale = max(_mad(residual), mad_floor)
    threshold = float(np.median(residual)) + k * scale
    return residual > threshold


def _wide_dark_rows(
    luminance: np.ndarray,
    wide: int,
    k: float,
    mad_floor: float = 1.0,
) -> np.ndarray:
    """Rows significantly darker than the wide luminance trend (black stripes)."""
    baseline = _median_filter_1d(luminance, wide)
    darker = baseline - luminance
    scale = max(_mad(darker), mad_floor)
    threshold = float(np.median(darker)) + k * scale
    return darker > threshold


def _row_profile_green(rgb: np.ndarray) -> np.ndarray:
    g = rgb[:, :, 1].mean(axis=1)
    r = rgb[:, :, 0].mean(axis=1)
    b = rgb[:, :, 2].mean(axis=1)
    return g - 0.5 * (r + b)


def _row_profile_luma(rgb: np.ndarray) -> np.ndarray:
    r = rgb[:, :, 0].mean(axis=1)
    g = rgb[:, :, 1].mean(axis=1)
    b = rgb[:, :, 2].mean(axis=1)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _periodic_stripe_metrics(profile: np.ndarray, height: int) -> FramePeriodic:
    """Detect dense periodic horizontal stripes via normalized autocorrelation."""
    min_lag = 6
    max_lag = min(64, height // 3)
    if max_lag < min_lag:
        return FramePeriodic()

    x = profile.astype(np.float64)
    x_centered = x - float(np.mean(x))
    corrs: list[float] = []
    best_lag = min_lag
    best_corr = -2.0
    for lag in range(min_lag, max_lag + 1):
        a = x_centered[:-lag]
        b = x_centered[lag:]
        denom = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
        if denom < 1e-12:
            corr = 0.0
        else:
            corr = float(np.dot(a, b) / denom)
        corrs.append(corr)
        if corr > best_corr + 1e-12 or (
            abs(corr - best_corr) <= 1e-12 and lag < best_lag
        ):
            best_corr = corr
            best_lag = lag

    median_corr = float(np.median(corrs))
    amplitude = float(np.percentile(x, 95) - np.percentile(x, 5))
    corr_peak = best_corr - median_corr
    detected = (
        best_lag >= 8
        and best_corr >= 0.90
        and corr_peak >= 0.50
        and amplitude >= 5.0
    )
    return FramePeriodic(
        detected=detected,
        lag=best_lag,
        best_corr=best_corr,
        median_corr=median_corr,
        amplitude=amplitude,
        channel="",
    )


def _analyze_frame_periodic(rgb: np.ndarray) -> FramePeriodic:
    height = rgb.shape[0]
    green_prof = _row_profile_green(rgb)
    if float(np.max(np.abs(green_prof))) < 1.0:
        channel = "luma"
        prof = _row_profile_luma(rgb)
    else:
        channel = "green"
        prof = green_prof
    result = _periodic_stripe_metrics(prof, height)
    result.channel = channel
    return result


def _group_rows(mask: np.ndarray, min_band_height: int) -> list[tuple[int, int, int]]:
    bands: list[tuple[int, int, int]] = []
    h = len(mask)
    y = 0
    while y < h:
        if not mask[y]:
            y += 1
            continue
        y0 = y
        while y < h and mask[y]:
            y += 1
        y1 = y
        height = y1 - y0
        if height >= min_band_height:
            bands.append((y0, y1, height))
    return bands


def analyze_frame_array(
    arr: np.ndarray,
    *,
    min_band_height: int = 8,
    k: float = 6.0,
    smooth_window: int = 5,
) -> FrameBands:
    """Detect horizontal bands in a single RGB uint8 frame."""
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise ValueError("arr must be an HxWx3 RGB uint8 array")
    rgb = arr[..., :3].astype(np.float64)
    r = rgb[:, :, 0].mean(axis=1)
    g = rgb[:, :, 1].mean(axis=1)
    b = rgb[:, :, 2].mean(axis=1)
    luminance = (r + g + b) / 3.0
    row_std = np.std(rgb, axis=1).mean(axis=1)
    green_dom = g - (r + b) / 2.0
    height = len(luminance)
    wide = _wide_window(height, smooth_window)

    anomalous = np.zeros(height, dtype=bool)
    for profile in (luminance, row_std, green_dom):
        anomalous |= _rows_exceeding_threshold(profile, smooth_window, k)
    anomalous |= _wide_profile_anomaly(green_dom, wide, k)
    anomalous |= _wide_dark_rows(luminance, wide, k)

    bands = _group_rows(anomalous, min_band_height)
    band_count = len(bands)
    total_height = sum(b[2] for b in bands)
    green_score = float(np.mean(green_dom))
    periodic = _analyze_frame_periodic(rgb)

    return FrameBands(
        bands=bands,
        band_count=band_count,
        total_band_height=total_height,
        green_dominance=green_score,
        periodic=periodic,
    )


def _compute_score(
    band_count: int,
    band_height_px: float,
    affected_frame_ratio: float,
) -> float:
    if band_count <= 0:
        return 0.0
    height_term = (band_height_px / 24.0) * 45.0
    ratio_term = affected_frame_ratio * 55.0
    return float(min(100.0, height_term + ratio_term))


def _compute_periodic_score(
    periodic_frame_ratio: float,
    best_corr: float,
    amplitude: float,
) -> float:
    if periodic_frame_ratio <= 0.0:
        return 0.0
    corr_term = max(0.0, (best_corr - 0.90) / 0.10) * 35.0
    amp_term = min(25.0, (amplitude / 40.0) * 25.0)
    ratio_term = periodic_frame_ratio * 40.0
    return float(min(100.0, corr_term + amp_term + ratio_term))


def analyze_frames(
    frames: list[np.ndarray],
    *,
    min_consecutive_frames: int = 2,
    min_band_height: int = 8,
    k: float = 6.0,
    smooth_window: int = 5,
) -> BandReport:
    """Analyze a sequence of RGB frames for persistent horizontal bands."""
    if not frames:
        return BandReport(frames_analyzed=0)

    per_frame: list[FrameBands] = [
        analyze_frame_array(
            f,
            min_band_height=min_band_height,
            k=k,
            smooth_window=smooth_window,
        )
        for f in frames
    ]

    range_counts: dict[tuple[int, int], int] = {}
    range_frames: dict[tuple[int, int], set[int]] = {}
    for idx, fb in enumerate(per_frame):
        for y0, y1, _ in fb.bands:
            key = (y0, y1)
            range_counts[key] = range_counts.get(key, 0) + 1
            range_frames.setdefault(key, set()).add(idx)

    confirmed: list[tuple[int, int, int, set[int]]] = []
    for (y0, y1), count in range_counts.items():
        if count >= min_consecutive_frames:
            height = y1 - y0
            confirmed.append((y0, y1, height, range_frames[(y0, y1)]))

    band_count = len(confirmed)
    heights = [c[2] for c in confirmed]
    band_height_px = float(np.median(heights)) if heights else 0.0

    affected_indices: set[int] = set()
    for _, _, _, frame_ids in confirmed:
        affected_indices |= frame_ids
    affected_frame_ratio = len(affected_indices) / len(frames)

    band_score = _compute_score(band_count, band_height_px, affected_frame_ratio)

    periodic_frames = [fb for fb in per_frame if fb.periodic.detected]
    periodic_frame_ratio = len(periodic_frames) / len(frames)
    periodic_detected = len(periodic_frames) > 0
    if periodic_frames:
        rep = max(periodic_frames, key=lambda fb: fb.periodic.best_corr)
        p = rep.periodic
        periodic_lag = float(p.lag)
        periodic_best_corr = p.best_corr
        periodic_median_corr = p.median_corr
        periodic_amplitude = p.amplitude
        periodic_channel = p.channel
    else:
        periodic_lag = 0.0
        periodic_best_corr = 0.0
        periodic_median_corr = 0.0
        periodic_amplitude = 0.0
        periodic_channel = ""

    periodic_score = _compute_periodic_score(
        periodic_frame_ratio,
        periodic_best_corr,
        periodic_amplitude,
    )
    score = float(max(band_score, periodic_score))

    if periodic_detected:
        verdict = "periodic"
    elif band_count > 0:
        verdict = "banding"
    else:
        verdict = "clean"

    green_dominance = float(np.mean([fb.green_dominance for fb in per_frame]))

    bands_out = [
        {
            "y0": y0,
            "y1": y1,
            "height": height,
            "frames": len(frame_ids),
        }
        for y0, y1, height, frame_ids in sorted(confirmed, key=lambda x: x[0])
    ]

    return BandReport(
        band_count=band_count,
        band_height_px=band_height_px,
        affected_frame_ratio=affected_frame_ratio,
        score=score,
        verdict=verdict,
        bands=bands_out,
        frames_analyzed=len(frames),
        green_dominance=green_dominance,
        periodic=periodic_detected,
        periodic_lag=periodic_lag,
        periodic_best_corr=periodic_best_corr,
        periodic_median_corr=periodic_median_corr,
        periodic_amplitude=periodic_amplitude,
        periodic_channel=periodic_channel,
        periodic_frame_ratio=periodic_frame_ratio,
    )


def _ffprobe_video(path: Path) -> dict[str, Any]:
    _require_tool("ffprobe")
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def _extract_frames(video: Path, fps: float, dest: Path) -> list[Path]:
    _require_tool("ffmpeg")
    pattern = str(dest / "frame_%06d.png")
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-vf",
            f"fps={fps}",
            pattern,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extraction failed: {proc.stderr.strip()}")
    frames = sorted(dest.glob("frame_*.png"))
    if not frames:
        raise RuntimeError(f"ffmpeg produced no frames for {video}")
    return frames


def _load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as img:
        return np.asarray(img.convert("RGB"))


def analyze_video(
    path: str | Path,
    *,
    fps: float = 2.0,
    min_consecutive_frames: int = 2,
    min_band_height: int = 8,
    k: float = 6.0,
    smooth_window: int = 5,
) -> dict[str, Any]:
    """Probe video metadata, sample frames, and return a banding report dict."""
    video = Path(path)
    if not video.is_file():
        raise FileNotFoundError(f"Video not found: {video}")

    meta = _ffprobe_video(video)
    width = height = 0
    duration = 0.0
    for stream in meta.get("streams", []):
        if stream.get("codec_type") == "video":
            width = int(stream.get("width", 0))
            height = int(stream.get("height", 0))
            break
    fmt = meta.get("format", {})
    try:
        duration = float(fmt.get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0

    tmpdir = tempfile.mkdtemp(prefix="banding_frames_")
    try:
        frame_paths = _extract_frames(video, fps, Path(tmpdir))
        arrays = [_load_rgb(p) for p in frame_paths]
        report = analyze_frames(
            arrays,
            min_consecutive_frames=min_consecutive_frames,
            min_band_height=min_band_height,
            k=k,
            smooth_window=smooth_window,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    out = report.to_dict()
    out.update(
        {
            "input": str(video),
            "width": width,
            "height": height,
            "duration_sec": duration,
            "fps_sample": fps,
        }
    )
    return out


def analyze_paths(
    paths: list[str | Path],
    *,
    fps: float = 2.0,
    min_consecutive_frames: int = 2,
    min_band_height: int = 8,
    k: float = 6.0,
    smooth_window: int = 5,
) -> dict[str, Any]:
    """Analyze image paths or a single video path."""
    if not paths:
        raise ValueError("paths must not be empty")

    resolved = [Path(p) for p in paths]
    if len(resolved) == 1 and resolved[0].suffix.lower() in VIDEO_EXTENSIONS:
        return analyze_video(
            resolved[0],
            fps=fps,
            min_consecutive_frames=min_consecutive_frames,
            min_band_height=min_band_height,
            k=k,
            smooth_window=smooth_window,
        )

    frames: list[np.ndarray] = []
    for p in resolved:
        if p.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {p}")
        frames.append(_load_rgb(p))

    report = analyze_frames(
        frames,
        min_consecutive_frames=min_consecutive_frames,
        min_band_height=min_band_height,
        k=k,
        smooth_window=smooth_window,
    )
    out = report.to_dict()
    out["input"] = str(resolved[0].parent) if len(resolved) > 1 else str(resolved[0])
    out["paths"] = [str(p) for p in resolved]
    return out


def _format_summary(data: dict[str, Any]) -> str:
    lines = [
        f"Input: {data.get('input', '?')}",
        f"Frames analyzed: {data.get('frames_analyzed', 0)}",
        f"Verdict: {data.get('verdict', '?')}",
        f"Score: {data.get('score', 0):.1f}/100",
        f"Green dominance: {data.get('green_dominance', 0):.2f}",
        f"Bands: {data.get('band_count', 0)} "
        f"(median height {data.get('band_height_px', 0):.1f}px, "
        f"affected frames {data.get('affected_frame_ratio', 0) * 100:.0f}%)",
    ]
    if data.get("periodic"):
        lines.append(
            f"Periodic stripes: lag={data.get('periodic_lag', 0):.0f}px "
            f"corr={data.get('periodic_best_corr', 0):.3f} "
            f"channel={data.get('periodic_channel', '?')} "
            f"({data.get('periodic_frame_ratio', 0) * 100:.0f}% frames)"
        )
    for band in data.get("bands", []):
        lines.append(
            f"  y={band['y0']}-{band['y1']} ({band['height']}px) "
            f"in {band['frames']} frame(s)"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect horizontal banding artifacts")
    parser.add_argument("input", help="Video file or image path")
    parser.add_argument("--fps", type=float, default=2.0, help="Frame sampling rate for video")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument("--min-band-height", type=int, default=8, dest="min_band_height")
    parser.add_argument("--k", type=float, default=6.0, help="MAD multiplier for row outliers")
    parser.add_argument("--min-consecutive-frames", type=int, default=2, dest="min_consecutive")
    args = parser.parse_args(argv)

    path = Path(args.input)
    if path.suffix.lower() in VIDEO_EXTENSIONS:
        report = analyze_video(
            path,
            fps=args.fps,
            min_band_height=args.min_band_height,
            k=args.k,
            min_consecutive_frames=args.min_consecutive,
        )
    else:
        report = analyze_paths(
            [path],
            fps=args.fps,
            min_band_height=args.min_band_height,
            k=args.k,
            min_consecutive_frames=args.min_consecutive,
        )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(_format_summary(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
