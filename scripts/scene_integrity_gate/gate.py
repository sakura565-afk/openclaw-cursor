"""Measure abrupt scene changes without confusing them with a smooth camera zoom.

The gate deliberately combines appearance, structure and skin occupancy.  A single
colour change (for example a light flash) is not enough to reject a clip.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

import cv2
import numpy as np

_HIST_BINS = (16, 8, 8)
_HIST_RANGES = (0, 180, 0, 256, 0, 256)
_GRID_SIZE = 4


@dataclass(frozen=True)
class GateConfig:
    """Thresholds are calibrated on the archived H3 corpus, not universal constants."""

    analysis_width: int = 160
    analysis_height: int = 160
    scene_score_threshold: float = 0.32
    histogram_min: float = 0.22
    edge_support_min: float = 0.38
    skin_support_min: float = 0.12
    abruptness_min: float = 2.5
    skin_jump_min: float = 0.12
    context_drop_min: float = 0.15
    object_drop_min: float = 0.18


@dataclass
class _Descriptor:
    histogram: np.ndarray
    edge_map: np.ndarray
    skin_fraction: float
    edge_density: float
    context_fraction: float = 0.0
    object_support: float = 1.0


@dataclass
class _ReferenceModel:
    context_lut: np.ndarray
    patch_histograms: list[np.ndarray | None]
    patch_weights: np.ndarray


def _validate_frame(frame: np.ndarray) -> np.ndarray:
    array = np.asarray(frame)
    if array.ndim != 3 or array.shape[2] != 3 or not array.size:
        raise ValueError("frames must be non-empty HxWx3 RGB arrays")
    if array.dtype != np.uint8:
        raise ValueError("frames must have uint8 RGB pixels")
    return array


def _histogram(hsv: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
    histogram = cv2.calcHist(
        [hsv], [0, 1, 2], mask, list(_HIST_BINS), list(_HIST_RANGES)
    ).astype(np.float32)
    total = float(histogram.sum())
    if total > 0.0:
        histogram /= total
    return histogram


def _skin_mask(rgb: np.ndarray) -> np.ndarray:
    ycrcb = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    y, cr, cb = cv2.split(ycrcb)
    hue, saturation, value = cv2.split(hsv)
    chroma = (cr >= 133) & (cr <= 180) & (cb >= 70) & (cb <= 135)
    visible = (y > 35) & (value > 40) & (saturation > 15)
    hue_ok = (hue <= 28) | (hue >= 170)
    return (chroma & visible & hue_ok).astype(np.uint8) * 255


def _base_descriptor(frame: np.ndarray, config: GateConfig) -> tuple[_Descriptor, np.ndarray, np.ndarray]:
    rgb = cv2.resize(
        _validate_frame(frame),
        (config.analysis_width, config.analysis_height),
        interpolation=cv2.INTER_AREA,
    )
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(gray, 60, 140)
    # One-pixel tolerance prevents tiny sub-pixel motion looking like a new structure.
    tolerant_edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    skin = _skin_mask(rgb)
    descriptor = _Descriptor(
        histogram=_histogram(hsv),
        edge_map=(tolerant_edges > 0).astype(np.float32),
        skin_fraction=float(np.mean(skin > 0)),
        edge_density=float(np.mean(edges > 0)),
    )
    return descriptor, hsv, skin


def _build_reference(hsv: np.ndarray, skin: np.ndarray, descriptor: _Descriptor) -> _ReferenceModel:
    context_mask = cv2.bitwise_not(skin)
    context_histogram = _histogram(hsv, context_mask)
    # Bins occupied by at least 0.1% of the reference context are characteristic
    # clothing/background colours; rare compression-noise bins are ignored.
    context_lut = context_histogram >= 0.001
    patch_histograms: list[np.ndarray | None] = []
    weights: list[float] = []
    height, width = skin.shape
    for row in range(_GRID_SIZE):
        for column in range(_GRID_SIZE):
            y1, y2 = row * height // _GRID_SIZE, (row + 1) * height // _GRID_SIZE
            x1, x2 = column * width // _GRID_SIZE, (column + 1) * width // _GRID_SIZE
            patch_skin = skin[y1:y2, x1:x2]
            patch_edges = descriptor.edge_map[y1:y2, x1:x2]
            context_share = float(np.mean(patch_skin == 0))
            edge_share = float(np.mean(patch_edges > 0))
            if context_share >= 0.35 and edge_share >= 0.01:
                patch_mask = np.zeros_like(skin)
                patch_mask[y1:y2, x1:x2] = 255
                patch_histograms.append(_histogram(hsv, patch_mask))
                weights.append(context_share * max(edge_share, 0.01))
            else:
                patch_histograms.append(None)
                weights.append(0.0)
    weight_array = np.asarray(weights, dtype=np.float32)
    if float(weight_array.sum()) > 0:
        weight_array /= float(weight_array.sum())
    return _ReferenceModel(context_lut, patch_histograms, weight_array)


def _apply_reference(
    descriptor: _Descriptor, hsv: np.ndarray, reference: _ReferenceModel
) -> None:
    bins = (
        (hsv[..., 0].astype(np.int32) * _HIST_BINS[0] // 180),
        (hsv[..., 1].astype(np.int32) * _HIST_BINS[1] // 256),
        (hsv[..., 2].astype(np.int32) * _HIST_BINS[2] // 256),
    )
    bins = tuple(np.clip(channel, 0, size - 1) for channel, size in zip(bins, _HIST_BINS))
    descriptor.context_fraction = float(np.mean(reference.context_lut[bins]))
    height, width = hsv.shape[:2]
    supports: list[float] = []
    for index, reference_histogram in enumerate(reference.patch_histograms):
        if reference_histogram is None:
            continue
        row, column = divmod(index, _GRID_SIZE)
        y1, y2 = row * height // _GRID_SIZE, (row + 1) * height // _GRID_SIZE
        x1, x2 = column * width // _GRID_SIZE, (column + 1) * width // _GRID_SIZE
        current_histogram = _histogram(hsv[y1:y2, x1:x2])
        distance = cv2.compareHist(reference_histogram, current_histogram, cv2.HISTCMP_BHATTACHARYYA)
        supports.append(float(np.clip(1.0 - distance, 0.0, 1.0)) * float(reference.patch_weights[index]))
    descriptor.object_support = float(np.sum(supports)) if supports else 1.0


def describe_frames(frames: Iterable[np.ndarray], config: GateConfig) -> list[_Descriptor]:
    descriptors: list[_Descriptor] = []
    reference: _ReferenceModel | None = None
    for frame in frames:
        descriptor, hsv, skin = _base_descriptor(frame, config)
        if reference is None:
            reference = _build_reference(hsv, skin, descriptor)
        _apply_reference(descriptor, hsv, reference)
        descriptors.append(descriptor)
    return descriptors


def _distances(descriptors: Sequence[_Descriptor]) -> list[dict[str, float | int]]:
    rows: list[dict[str, float | int]] = []
    for index in range(1, len(descriptors)):
        previous, current = descriptors[index - 1], descriptors[index]
        histogram = float(
            cv2.compareHist(previous.histogram, current.histogram, cv2.HISTCMP_BHATTACHARYYA)
        )
        intersection = float(np.minimum(previous.edge_map, current.edge_map).sum())
        denominator = float(previous.edge_map.sum() + current.edge_map.sum())
        edge = 1.0 - 2.0 * intersection / denominator if denominator > 0 else 0.0
        skin = abs(current.skin_fraction - previous.skin_fraction)
        context_drop = max(0.0, previous.context_fraction - current.context_fraction)
        object_drop = max(0.0, previous.object_support - current.object_support)
        score = 0.45 * histogram + 0.35 * edge + 0.20 * skin
        rows.append(
            {
                "frame": index,
                "histogram_distance": histogram,
                "edge_distance": float(edge),
                "skin_fraction_previous": previous.skin_fraction,
                "skin_fraction": current.skin_fraction,
                "skin_jump": float(skin),
                "context_previous": previous.context_fraction,
                "context_fraction": current.context_fraction,
                "context_drop": float(context_drop),
                "object_support_previous": previous.object_support,
                "object_support": current.object_support,
                "object_drop": float(object_drop),
                "edge_density_previous": previous.edge_density,
                "edge_density": current.edge_density,
                "scene_score": float(score),
            }
        )
    scores = np.asarray([row["scene_score"] for row in rows], dtype=np.float32)
    for index, row in enumerate(rows):
        neighbours = np.concatenate((scores[max(0, index - 5):max(0, index - 1)], scores[index + 2:index + 6]))
        baseline = float(np.median(neighbours)) if neighbours.size else 0.0
        row["local_baseline"] = baseline
        row["abruptness"] = float(row["scene_score"]) / max(baseline, 0.03)
    return rows


def _reason(code: str, row: dict[str, float | int], value: float, threshold: float) -> dict[str, object]:
    return {
        "code": code,
        "frame": int(row["frame"]),
        "metric": "scene_change" if code == "abrupt_scene_change" else "figure_disappeared",
        "value": float(value),
        "jump": float(value),
        "threshold": float(threshold),
        "measurements": dict(row),
    }


def evaluate_descriptors(descriptors: Sequence[_Descriptor], config: GateConfig) -> dict[str, object]:
    transitions = _distances(descriptors)
    candidates: list[dict[str, object]] = []
    for row in transitions:
        scene_change = (
            float(row["scene_score"]) >= config.scene_score_threshold
            and float(row["histogram_distance"]) >= config.histogram_min
            and (
                float(row["edge_distance"]) >= config.edge_support_min
                or float(row["skin_jump"]) >= config.skin_support_min
            )
            and float(row["abruptness"]) >= config.abruptness_min
        )
        if scene_change:
            candidates.append(_reason("abrupt_scene_change", row, float(row["scene_score"]), config.scene_score_threshold))
        figure_disappeared = (
            float(row["skin_jump"]) >= config.skin_jump_min
            and float(row["context_drop"]) >= config.context_drop_min
            and float(row["object_drop"]) >= config.object_drop_min
            and float(row["abruptness"]) >= config.abruptness_min
        )
        if figure_disappeared:
            candidates.append(_reason("figure_disappeared", row, float(row["object_drop"]), config.object_drop_min))
    # A 1-3 frame transition is one event, not several independent failures.
    reasons: list[dict[str, object]] = []
    for candidate in sorted(candidates, key=lambda item: float(item["value"]), reverse=True):
        if any(
            candidate["code"] == existing["code"]
            and abs(int(candidate["frame"]) - int(existing["frame"])) <= 2
            for existing in reasons
        ):
            continue
        reasons.append(candidate)
    reasons.sort(key=lambda item: (int(item["frame"]), str(item["code"])))
    skin_values = np.asarray([descriptor.skin_fraction for descriptor in descriptors], dtype=np.float32)
    third = max(1, len(skin_values) // 3)
    skin_growth = float(np.median(skin_values[-third:]) - np.median(skin_values[:third]))
    if skin_values.size > 2 and float(np.std(skin_values)) > 0:
        skin_trend = float(np.corrcoef(np.arange(skin_values.size), skin_values)[0, 1])
    else:
        skin_trend = 0.0
    smooth_zoom = not reasons and abs(skin_growth) >= 0.05 and abs(skin_trend) >= 0.70
    return {
        "schema_version": 1,
        "frame_count": len(descriptors),
        "verdict": "reject" if reasons else "accept",
        "accepted": not reasons,
        "camera_motion": "smooth_zoom" if smooth_zoom else "static_or_continuous",
        "skin_fraction_trend": skin_growth,
        "skin_trend_correlation": skin_trend,
        "reasons": reasons,
        "transitions": transitions,
        "thresholds": asdict(config),
    }


def analyze_frames(frames: Sequence[np.ndarray], *, config: GateConfig | None = None) -> dict[str, object]:
    """Analyze decoded RGB frames; frame numbers in reasons are zero-based."""
    settings = config or GateConfig()
    if len(frames) < 2:
        raise ValueError("at least two frames are required")
    first_shape = _validate_frame(frames[0]).shape
    if any(_validate_frame(frame).shape != first_shape for frame in frames):
        raise ValueError("all frames must have identical dimensions")
    return evaluate_descriptors(describe_frames(frames, settings), settings)


def analyze_video(video: str | Path, *, config: GateConfig | None = None) -> dict[str, object]:
    """Decode and analyze a video without keeping full-resolution frames in memory."""
    settings = config or GateConfig()
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise ValueError(f"cannot open video: {video}")
    fps = float(capture.get(cv2.CAP_PROP_FPS))

    def decoded() -> Iterable[np.ndarray]:
        try:
            while True:
                ok, bgr = capture.read()
                if not ok:
                    break
                yield cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        finally:
            capture.release()

    descriptors = describe_frames(decoded(), settings)
    if len(descriptors) < 2:
        raise ValueError(f"video has fewer than two decodable frames: {video}")
    report = evaluate_descriptors(descriptors, settings)
    report.update(video=Path(video).name, fps=fps)
    return report
