"""Sparse pore-pit fields with a darker centre and a lighter surrounding rim."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from scipy import ndimage

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from probe_pore_spectrum import band_energy


def build_hsv_skin_mask(frame_bgr: np.ndarray) -> np.ndarray:
    """Build a uint8 skin prior, excluding foam and disconnected small blobs."""
    if frame_bgr.dtype != np.uint8 or frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
        raise ValueError("frame_bgr must be a uint8 HxWx3 image")
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    skin_hue = (hue <= 25) | (hue >= 170)
    # OpenCV's uint8 S/V range is 0..255. These comparisons deliberately
    # happen before conversion to float, avoiding the old [0,1] threshold bug.
    not_foam = (value <= np.uint8(242)) & (saturation >= np.uint8(26))
    candidate = (skin_hue & not_foam & (value >= 40)).astype(np.uint8) * 255
    candidate = cv2.morphologyEx(
        candidate, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8)
    )
    candidate = cv2.morphologyEx(
        candidate, cv2.MORPH_CLOSE, np.ones((5, 5), dtype=np.uint8)
    )

    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, connectivity=8)
    if count <= 1:
        return np.zeros(frame_bgr.shape[:2], dtype=np.uint8)
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    mask = np.where(labels == largest, 255, 0).astype(np.uint8)
    mask = cv2.GaussianBlur(mask, (5, 5), sigmaX=1.2, sigmaY=1.2)
    # Closing may fill a one-pixel foam hole; enforce the photometric cutoff
    # once more on the final soft mask so foam can never be reintroduced.
    mask[~not_foam] = 0
    return mask


def local_structure_amplitude(frame_bgr: np.ndarray, threshold: float) -> np.ndarray:
    """Measure local RMS high-frequency structure and subtract a hard cutoff.

    Values are in normalised image-intensity units. Smooth regions are exactly
    zero; there is deliberately no constant detail floor.
    """
    if frame_bgr.dtype != np.uint8 or frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
        raise ValueError("frame_bgr must be a uint8 HxWx3 image")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be in [0, 1]")
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    low = cv2.GaussianBlur(gray, (0, 0), sigmaX=2.0, sigmaY=2.0)
    high = gray - low
    local_mean_square = cv2.GaussianBlur(high * high, (0, 0), sigmaX=2.0, sigmaY=2.0)
    local_rms = np.sqrt(np.maximum(local_mean_square, 0.0))
    return np.maximum(local_rms - float(threshold), 0.0).astype(np.float32)


def _blur_impulses(impulses: np.ndarray, sigma: float) -> np.ndarray:
    """Return a Gaussian pit kernel normalised to one at an isolated centre."""
    radius = max(1, int(np.ceil(2.5 * sigma)))
    kernel_size = 2 * radius + 1
    blurred = cv2.GaussianBlur(
        impulses,
        (kernel_size, kernel_size),
        sigmaX=sigma,
        sigmaY=sigma,
        borderType=cv2.BORDER_CONSTANT,
    )
    isolated = np.zeros_like(impulses)
    isolated[radius, radius] = 1.0
    peak = float(
        cv2.GaussianBlur(
            isolated,
            (kernel_size, kernel_size),
            sigmaX=sigma,
            sigmaY=sigma,
            borderType=cv2.BORDER_CONSTANT,
        )[radius, radius]
    )
    return blurred / max(peak, 1e-8)


def _single_octave(
    shape: tuple[int, int],
    density: float,
    rim: float,
    sigma: float,
    rng: np.random.Generator,
) -> np.ndarray:
    impulses = (rng.random(shape, dtype=np.float32) < density).astype(np.float32)
    if not np.any(impulses):
        return np.zeros(shape, dtype=np.float32)
    centre = _blur_impulses(impulses, sigma)
    surround = _blur_impulses(impulses, sigma * 1.8)
    return -centre + float(rim) * surround


def generate_pore_field(
    shape: tuple[int, int],
    pit_density: float,
    rim: float,
    pore_sigma: float,
    seed: int,
    two_octave: bool = True,
) -> np.ndarray:
    """Generate one deterministic field of sparse, signed pore profiles.

    ``pit_density`` is the probability of a pore centre at a pixel. There is
    no random value at non-centre pixels: their values come only from Gaussian
    pit/rim profiles. The optional second octave adds fewer, broader pores.
    """
    if len(shape) != 2 or min(shape) <= 0:
        raise ValueError("shape must contain two positive dimensions")
    if not 0.0 <= pit_density <= 1.0:
        raise ValueError("pit_density must be in [0, 1]")
    if rim < 0.0:
        raise ValueError("rim must be non-negative")
    if pore_sigma <= 0.0:
        raise ValueError("pore_sigma must be positive")

    rng = np.random.default_rng(seed)
    field = _single_octave(shape, pit_density, rim, pore_sigma, rng)
    if two_octave:
        coarse = _single_octave(shape, pit_density * 0.45, rim, pore_sigma * 1.9, rng)
        field = 0.72 * field + 0.28 * coarse

    peak = float(np.max(np.abs(field)))
    if peak > 0.0:
        field = field / peak
        # Gaussian tails far away from a centre are not visible pores. Removing
        # only these negligible tails keeps the inactive part genuinely flat.
        field[np.abs(field) < 0.02] = 0.0
    return np.asarray(field, dtype=np.float32)


def _read_external_mask(path: Path, shape: tuple[int, int]) -> np.ndarray:
    mask = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise ValueError(f"cannot read mask PNG: {path}")
    if mask.shape != shape:
        raise ValueError(f"mask shape {mask.shape} does not match video shape {shape}")
    return mask


def _roi_from_mask(mask: np.ndarray) -> tuple[int, int, int, int]:
    """Return x0, y0, x1, y1 for a fixed, comparable spectral ROI."""
    height, width = mask.shape
    ys, xs = np.nonzero(mask > 0)
    if len(xs) == 0:
        return 0, 0, width, height
    padding = 4
    x0 = max(0, int(xs.min()) - padding)
    x1 = min(width, int(xs.max()) + padding + 1)
    y0 = max(0, int(ys.min()) - padding)
    y1 = min(height, int(ys.max()) + padding + 1)
    return x0, y0, x1, y1


def _flow_current_to_previous(current_gray: np.ndarray, previous_gray: np.ndarray) -> np.ndarray:
    # remap() samples the previous layer at destination + flow. Farneback must
    # therefore be computed current -> previous, not previous -> current.
    return cv2.calcOpticalFlowFarneback(
        current_gray,
        previous_gray,
        None,
        pyr_scale=0.5,
        levels=3,
        winsize=21,
        iterations=3,
        poly_n=5,
        poly_sigma=1.2,
        flags=0,
    )


def _warp_layer(
    layer: np.ndarray,
    flow: np.ndarray,
    grid_x: np.ndarray,
    grid_y: np.ndarray,
) -> np.ndarray:
    return cv2.remap(
        layer,
        grid_x + flow[..., 0],
        grid_y + flow[..., 1],
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )


def _temporal_alpha(temporal_sigma: float) -> float:
    if temporal_sigma <= 0.0:
        return 1.0
    return float(1.0 - np.exp(-0.5 / (temporal_sigma * temporal_sigma)))


def _percent_delta(before: float, after: float) -> float | None:
    if abs(before) <= 1e-12:
        return 0.0 if abs(after) <= 1e-12 else None
    return float((after / before - 1.0) * 100.0)


def _metric_pair(before: float, after: float) -> dict[str, float | None]:
    return {
        "before": float(before),
        "after": float(after),
        "delta_pct": _percent_delta(before, after),
    }


def measure_video(
    video: Path,
    roi: tuple[int, int, int, int],
    max_frames: int,
) -> dict[str, float | int]:
    """Measure actual decoded frames using the canonical 3--12 px pore band."""
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise ValueError(f"cannot open video: {video}")
    pore_values: list[float] = []
    rgb_values: list[float] = []
    hf_values: list[float] = []
    previous: np.ndarray | None = None
    previous_gray: np.ndarray | None = None
    frames = 0
    x0, y0, x1, y1 = roi
    try:
        while frames < max_frames:
            ok, frame = capture.read()
            if not ok:
                break
            current = frame.astype(np.float32)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
            crop = gray[y0:y1, x0:x1]
            if crop.size:
                pore_values.append(float(band_energy(crop, 3.0, 12.0)))
            if previous is not None:
                rgb_values.append(float(np.abs(current - previous).mean()))
            if previous_gray is not None:
                before_lap = ndimage.laplace(previous_gray)
                current_lap = ndimage.laplace(gray)
                hf_values.append(float(np.abs(current_lap - before_lap).mean()))
            previous = current
            previous_gray = gray
            frames += 1
    finally:
        capture.release()
    return {
        "frames": frames,
        "pore_band_energy": float(np.mean(pore_values)) if pore_values else 0.0,
        "temporal_rgb_delta": float(np.mean(rgb_values)) if rgb_values else 0.0,
        "temporal_hf_delta": float(np.mean(hf_values)) if hf_values else 0.0,
    }


def process_video(
    video: str | Path,
    out: str | Path,
    strength: float = 2.0,
    pit_density: float = 0.003,
    rim: float = 0.35,
    pore_sigma: float = 1.25,
    threshold: float = 0.008,
    mask: str | Path | None = None,
    temporal_sigma: float = 1.5,
    seed: int = 0,
    two_octave: bool = True,
    max_frames: int | None = None,
) -> dict:
    """Inject one warped pore field into an MP4 and write measured JSON metrics."""
    source_path = Path(video)
    output_path = Path(out)
    if not source_path.is_file():
        raise ValueError(f"video does not exist: {source_path}")
    if strength < 0.0:
        raise ValueError("strength must be non-negative")
    if not 0.0 <= pit_density <= 1.0:
        raise ValueError("pit_density must be in [0, 1]")
    if rim < 0.0 or pore_sigma <= 0.0:
        raise ValueError("rim must be non-negative and pore_sigma positive")
    if not 0.0 <= threshold <= 1.0 or temporal_sigma < 0.0:
        raise ValueError("threshold must be in [0, 1] and temporal_sigma non-negative")
    if max_frames is not None and max_frames <= 0:
        raise ValueError("max_frames must be positive")
    mask_path = Path(mask) if mask is not None else None
    if mask_path is not None and not mask_path.is_file():
        raise ValueError(f"mask does not exist: {mask_path}")

    capture = cv2.VideoCapture(str(source_path))
    if not capture.isOpened():
        raise ValueError(f"cannot open video: {source_path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    if not np.isfinite(fps) or fps <= 0.0:
        fps = 25.0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer: cv2.VideoWriter | None = None
    field: np.ndarray | None = None
    previous_gray: np.ndarray | None = None
    previous_injection: np.ndarray | None = None
    external_mask: np.ndarray | None = None
    metric_roi: tuple[int, int, int, int] | None = None
    coverage_sum = 0.0
    frames_processed = 0
    alpha = _temporal_alpha(temporal_sigma)

    try:
        while max_frames is None or frames_processed < max_frames:
            ok, frame = capture.read()
            if not ok:
                break
            height, width = frame.shape[:2]
            if writer is None:
                writer = cv2.VideoWriter(
                    str(output_path),
                    cv2.VideoWriter_fourcc(*"mp4v"),
                    fps,
                    (width, height),
                )
                if not writer.isOpened():
                    raise RuntimeError(f"cannot open MP4 writer: {output_path}")
                grid_y, grid_x = np.mgrid[0:height, 0:width].astype(np.float32)
            if external_mask is None and mask_path is not None:
                external_mask = _read_external_mask(mask_path, (height, width))
            mask_u8 = external_mask if external_mask is not None else build_hsv_skin_mask(frame)
            mask_float = mask_u8.astype(np.float32) / 255.0
            coverage_sum += float(np.count_nonzero(mask_u8)) / mask_u8.size
            if metric_roi is None and np.any(mask_u8):
                metric_roi = _roi_from_mask(mask_u8)

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            flow = None
            if field is None:
                field = generate_pore_field(
                    (height, width), pit_density, rim, pore_sigma, seed, two_octave
                )
            else:
                assert previous_gray is not None
                flow = _flow_current_to_previous(gray, previous_gray)
                field = _warp_layer(field, flow, grid_x, grid_y)

            amplitude = local_structure_amplitude(frame, threshold)
            raw_injection = strength * 255.0 * field * amplitude * mask_float
            if previous_injection is None:
                injection = raw_injection.astype(np.float32)
            else:
                assert flow is not None
                carried = _warp_layer(previous_injection, flow, grid_x, grid_y)
                injection = alpha * raw_injection + (1.0 - alpha) * carried
                injection *= mask_float
            output_frame = np.clip(
                frame.astype(np.float32) + injection[..., None], 0.0, 255.0
            ).astype(np.uint8)
            writer.write(output_frame)
            previous_gray = gray
            previous_injection = injection.astype(np.float32)
            frames_processed += 1
    finally:
        capture.release()
        if writer is not None:
            writer.release()

    if frames_processed == 0:
        raise ValueError("input video has no decodable frames")
    if metric_roi is None:
        metric_roi = (0, 0, width, height)

    # Measure the file that was actually encoded, not an ideal in-memory frame.
    before = measure_video(source_path, metric_roi, frames_processed)
    after = measure_video(output_path, metric_roi, frames_processed)
    report = {
        "video": str(source_path),
        "output": str(output_path),
        "frames_processed": frames_processed,
        "fps": fps,
        "mask_coverage_pct": float(100.0 * coverage_sum / frames_processed),
        "metric_roi_xyxy": list(metric_roi),
        "pore_band_energy": _metric_pair(
            float(before["pore_band_energy"]), float(after["pore_band_energy"])
        ),
        "temporal_rgb_delta": _metric_pair(
            float(before["temporal_rgb_delta"]), float(after["temporal_rgb_delta"])
        ),
        "temporal_hf_delta": _metric_pair(
            float(before["temporal_hf_delta"]), float(after["temporal_hf_delta"])
        ),
        "parameters": {
            "strength": float(strength),
            "pit_density": float(pit_density),
            "rim": float(rim),
            "pore_sigma": float(pore_sigma),
            "threshold": float(threshold),
            "mask": str(mask_path) if mask_path is not None else None,
            "temporal_sigma": float(temporal_sigma),
            "seed": int(seed),
            "two_octave": bool(two_octave),
            "max_frames": max_frames,
        },
    }
    report_path = output_path.with_suffix(".json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
