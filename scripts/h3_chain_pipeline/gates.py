# -*- coding: utf-8 -*-
"""The per-chunk quality gate.

A chunk is measured, not trusted. Two numbers decide whether a chunk is
allowed into the clip:

* **face drift** - how far the face moved inside the face ROI between the
  chunk's first frame (the anchor) and its later frames. This is the number
  that exposes a "floating face": the drift grows towards the tail of the
  chunk, so the LAST frame is the interesting one.
* **texture** - the pore-band energy inside the same ROI. Texture is stable
  across seeds (two renders of the same scene measured 1% apart), so a chunk
  whose texture collapses relative to the first chunk is a broken take, not
  a stylistic variation.

Both are computed with the repository's own measurement code
(``scripts/probe_face_drift.py`` and ``scripts/compare_video_texture.py``)
imported as libraries, so the numbers mean the same thing here as they do
everywhere else in the repo.

Texture is compared RELATIVE to the first accepted chunk, never against an
absolute constant: the absolute pore energy depends on the resolution and on
the ROI, while the ratio between two chunks of the same clip does not.
"""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths

__all__ = [
    "ChunkMetrics",
    "GateDecision",
    "evaluate_chunk",
    "extract_frames",
    "face_drift_series",
    "measure_chunk",
    "measure_clip_drift",
]


def _face_norm() -> tuple[float, float, float, float]:
    """The face ROI, in normalised coordinates.

    Taken from ``scripts/probe_face_drift.py`` so both the per-chunk gate and
    the clip-level number use exactly the same box.
    """
    from scripts.probe_face_drift import FACE_NORM

    return FACE_NORM


def extract_frames(video: Path, out_dir: Path, indices: list[int]) -> list[Path]:
    """Extract exactly the frames with the given indices into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("f_*.png"):
        old.unlink()
    if not indices:
        return []
    wanted = sorted(set(indices))
    expression = "+".join(f"eq(n\\,{index})" for index in wanted)
    cmd = [
        paths.ffmpeg_exe(), "-y", "-v", "error", "-i", str(video),
        "-vf", f"select={expression}", "-fps_mode", "passthrough",
        "-start_number", "0", str(out_dir / "f_%05d.png"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    files = sorted(out_dir.glob("f_*.png"))
    if proc.returncode != 0 or len(files) != len(wanted):
        raise RuntimeError(
            f"could not extract {len(wanted)} frames from {video}: "
            f"{proc.stderr[:400]}"
        )
    return files


def face_drift_series(frames: list[Path]) -> dict[str, Any]:
    """Mean |RGB| difference inside the face ROI, every frame vs. the first.

    This is the metric of ``scripts/probe_face_drift.py``, computed on an
    already extracted frame list so a chunk is decoded only once.
    """
    import numpy as np
    from PIL import Image

    if len(frames) < 2:
        raise RuntimeError("need at least two frames to measure the drift")
    norm = _face_norm()
    drifts: list[float] = []
    reference: np.ndarray | None = None
    roi: tuple[int, int, int, int] = (0, 0, 0, 0)
    for path in frames:
        arr = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
        height, width = arr.shape[:2]
        x1, y1 = int(norm[0] * width), int(norm[1] * height)
        x2, y2 = int(norm[2] * width), int(norm[3] * height)
        roi = (x1, y1, x2, y2)
        patch = arr[y1:y2, x1:x2]
        if reference is None:
            reference = patch
            drifts.append(0.0)
        else:
            drifts.append(float(np.abs(patch - reference).mean()))
    values = np.array(drifts)
    return {
        "frames": len(values),
        "roi": list(roi),
        "face_drift_mean": round(float(values[1:].mean()), 4),
        "face_drift_max": round(float(values[1:].max()), 4),
        "face_drift_last": round(float(values[-1]), 4),
        # A frame still "holds" the face while its drift stays under 8/255.
        "frames_holding_face": int((values[1:] <= 8.0).sum()),
        "drift_series": [round(float(v), 3) for v in values],
    }


@dataclass
class ChunkMetrics:
    """Everything measured about one rendered chunk."""

    face_drift_mean: float = 0.0
    face_drift_max: float = 0.0
    face_drift_last: float = 0.0
    frames_holding_face: int = 0
    frames: int = 0
    pore: float = 0.0
    laplacian: float = 0.0
    rgb_delta: float = 0.0
    hf_delta: float = 0.0
    texture_ratio: float = 0.0
    """Pore energy relative to the baseline chunk (1.0 = the baseline)."""
    texture_measured: bool = False
    """Whether a texture comparison against the baseline was possible.

    False for the FIRST chunk (there is no baseline yet) and False when the
    pore metric could not be computed at all (no scipy, failed decode).
    The two are different: the first chunk is simply not comparable, the
    second one is an unknown that must not pass the gate.
    """
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "face_drift_mean": self.face_drift_mean,
            "face_drift_max": self.face_drift_max,
            "face_drift_last": self.face_drift_last,
            "frames_holding_face": self.frames_holding_face,
            "frames": self.frames,
            "pore": self.pore,
            "laplacian": self.laplacian,
            "rgb_delta": self.rgb_delta,
            "hf_delta": self.hf_delta,
            "texture_ratio": round(self.texture_ratio, 4),
            "notes": list(self.notes),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ChunkMetrics":
        known = {f: data.get(f) for f in (
            "face_drift_mean", "face_drift_max", "face_drift_last",
            "frames_holding_face", "frames", "pore", "laplacian",
            "rgb_delta", "hf_delta",
        )}
        metrics = cls(**{k: v for k, v in known.items() if v is not None})
        metrics.texture_ratio = float(data.get("texture_ratio", 0.0) or 0.0)
        metrics.notes = list(data.get("notes") or [])
        return metrics

    def score(self) -> tuple[float, float]:
        """Ranking key for "the best of the variants".

        The tail drift decides - that is the defect this pipeline exists to
        remove - and the texture ratio breaks ties.
        """
        return (self.face_drift_last, -self.texture_ratio)


@dataclass
class GateDecision:
    """The verdict of the gate for one chunk."""

    accepted: bool
    reason: str
    metrics: ChunkMetrics


def measure_chunk(video: Path, baseline_pore: float = 0.0) -> ChunkMetrics:
    """Measure one rendered chunk.

    ``baseline_pore`` is the pore energy of the first accepted chunk; the
    texture ratio of this chunk is measured against it. Pass 0 when there is
    no baseline yet - the ratio then stays 0 and the texture part of the gate
    is skipped.
    """
    import numpy as np

    metrics = ChunkMetrics()
    with tempfile.TemporaryDirectory(prefix="h3chain_gate_") as tmp:
        work = Path(tmp)
        # The whole chunk is decoded: the drift of the last frame is the
        # number that matters, and it can only be found by walking to the end.
        cmd = [
            paths.ffmpeg_exe(), "-y", "-v", "error", "-i", str(video),
            "-fps_mode", "passthrough", "-start_number", "0",
            str(work / "f_%05d.png"),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        frames = sorted(work.glob("f_*.png"))
        if proc.returncode != 0 or len(frames) < 2:
            raise RuntimeError(
                f"could not decode {video} for measurement: {proc.stderr[:400]}"
            )
        drift = face_drift_series(frames)
        metrics.frames = int(drift["frames"])
        metrics.face_drift_mean = float(drift["face_drift_mean"])
        metrics.face_drift_max = float(drift["face_drift_max"])
        metrics.face_drift_last = float(drift["face_drift_last"])
        metrics.frames_holding_face = int(drift["frames_holding_face"])

        try:
            from scripts.compare_video_texture import (
                canonical_sizes, measure,
            )

            rois = [("face", _face_norm())]
            canonical = canonical_sizes([str(p) for p in frames], rois=rois)
            texture = measure(
                [str(p) for p in frames], rois=rois,
                face_roi=_face_norm(), canonical=canonical,
            )
            metrics.pore = float(texture["pore"])
            metrics.laplacian = float(texture["laplacian"])
            metrics.rgb_delta = float(texture["rgb_delta"])
            metrics.hf_delta = float(texture["hf_delta"])
        except ImportError as exc:
            # scipy is needed by the pore metric. Without it the drift is
            # still measured and the texture part of the gate is skipped.
            metrics.notes.append(f"texture metric unavailable: {exc}")
            metrics.pore = 0.0

    if baseline_pore > 0 and metrics.pore > 0:
        metrics.texture_ratio = float(np.float64(metrics.pore) / baseline_pore)
        metrics.texture_measured = True
    return metrics


def measure_clip_drift(video: Path, max_frames: int = 0) -> dict[str, Any]:
    """Face drift over a WHOLE assembled clip.

    Uses ``scripts/probe_face_drift.py`` directly - this is the canonical
    number of the repository, the one that says whether the face held from
    the first frame to the last.
    """
    from scripts.probe_face_drift import drift_series

    return drift_series(video, max_frames)


def evaluate_chunk(
    metrics: ChunkMetrics,
    max_face_drift: float,
    min_texture_ratio: float = 0.0,
) -> GateDecision:
    """Decide whether a chunk may enter the clip.

    * the drift of the chunk's LAST frame must stay under the limit - the
      tail is where the face drifts away;
    * the texture must not fall below the ratio of the baseline chunk.
    """
    reasons: list[str] = []
    if metrics.face_drift_last > max_face_drift:
        reasons.append(
            f"face drift {metrics.face_drift_last:.2f} at the tail exceeds "
            f"the limit {max_face_drift:.2f}"
        )
    if metrics.face_drift_mean > max_face_drift:
        reasons.append(
            f"mean face drift {metrics.face_drift_mean:.2f} exceeds the "
            f"limit {max_face_drift:.2f}"
        )
    if min_texture_ratio > 0:
        # The texture gate fails CLOSED. A ratio that could not be computed
        # is not a pass, it is an unknown, and an unknown must not put a
        # drifting chunk into the clip.
        #
        # The FIRST chunk is the exception: it IS the baseline, there is
        # nothing to compare it against, so the check does not apply to it.
        # That is what texture_measured distinguishes.
        if metrics.texture_measured:
            if metrics.texture_ratio < min_texture_ratio:
                reasons.append(
                    f"texture {metrics.texture_ratio:.2f} of the baseline is "
                    f"below the limit {min_texture_ratio:.2f}"
                )
        elif metrics.pore <= 0:
            reasons.append(
                f"texture was not measured (pore {metrics.pore:.2f}); the "
                f"limit {min_texture_ratio:.2f} cannot be checked"
            )
    if reasons:
        return GateDecision(False, "; ".join(reasons), metrics)
    return GateDecision(True, "within the limits", metrics)
