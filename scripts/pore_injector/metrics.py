# -*- coding: utf-8 -*-
"""Metrics for the pore injector: what changed, measured against the input.

The pore band is defined in PIXELS, so the numbers here are only comparable
between clips of the same resolution. The band-energy implementation is
reused from ``scripts/probe_pore_spectrum.py`` on purpose: a second
implementation of the same metric would silently produce a different scale
and every comparison against our earlier numbers would be meaningless.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from probe_pore_spectrum import band_energy  # type: ignore  # noqa: E402

__all__ = [
    "pore_band_energy",
    "temporal_rgb_delta",
    "temporal_hf_delta",
    "compare_runs",
    "RunMetrics",
    "PORE_BAND_LO",
    "PORE_BAND_HI",
]

PORE_BAND_LO = 3.0
PORE_BAND_HI = 12.0


def _gray(frame: np.ndarray) -> np.ndarray:
    """Luma in 0..255, matching the scale used by the other metric scripts."""
    return (frame[:, :, 0].astype(np.float32) * 0.299
            + frame[:, :, 1].astype(np.float32) * 0.587
            + frame[:, :, 2].astype(np.float32) * 0.114) * 255.0


def _crop(gray: np.ndarray, roi: tuple[float, float, float, float] | None):
    if roi is None:
        return gray
    height, width = gray.shape
    x0, y0, x1, y1 = roi
    cropped = gray[int(y0 * height):int(y1 * height), int(x0 * width):int(x1 * width)]
    return cropped if cropped.size else None


def pore_band_energy(frames: list[np.ndarray],
                     roi: tuple[float, float, float, float] | None = None,
                     lo: float = PORE_BAND_LO, hi: float = PORE_BAND_HI) -> float:
    """Mean spectral energy of the 3-12 px band over the frames.

    ``roi`` is normalised (x0, y0, x1, y1); without it the whole frame is
    used.
    """
    if not frames:
        return 0.0
    values = []
    for frame in frames:
        gray = _crop(_gray(frame), roi)
        if gray is None:
            continue
        values.append(band_energy(gray, lo, hi))
    return float(np.mean(values)) if values else 0.0


def temporal_rgb_delta(frames: list[np.ndarray]) -> float:
    """Mean absolute frame-to-frame RGB difference."""
    if len(frames) < 2:
        return 0.0
    deltas = []
    for previous, current in zip(frames, frames[1:]):
        deltas.append(float(np.abs(current.astype(np.float32) - previous.astype(np.float32)).mean()))
    return float(np.mean(deltas))


def temporal_hf_delta(frames: list[np.ndarray]) -> float:
    """Mean frame-to-frame difference of the Laplacian (high-frequency motion)."""
    if len(frames) < 2:
        return 0.0
    from scipy import ndimage

    deltas = []
    previous = ndimage.laplace(_gray(frames[0]))
    for frame in frames[1:]:
        current = ndimage.laplace(_gray(frame))
        deltas.append(float(np.abs(current - previous).mean()))
        previous = current
    return float(np.mean(deltas))


class RunMetrics:
    """Accumulates the tracked metrics frame by frame.

    Only the previous frame is kept, so memory does not grow with clip
    length. The numbers match :func:`compare_runs` exactly.
    """

    def __init__(self, roi: tuple[float, float, float, float] | None = None) -> None:
        from scipy import ndimage

        self._ndimage = ndimage
        self.roi = roi
        self.pore_in: list[float] = []
        self.pore_out: list[float] = []
        self.rgb_in: list[float] = []
        self.rgb_out: list[float] = []
        self.hf_in: list[float] = []
        self.hf_out: list[float] = []
        self._prev_in: np.ndarray | None = None
        self._prev_out: np.ndarray | None = None
        self._prev_lap_in: np.ndarray | None = None
        self._prev_lap_out: np.ndarray | None = None
        self.frames = 0

    def update(self, frame_in: np.ndarray, frame_out: np.ndarray) -> None:
        gray_in = _gray(frame_in)
        gray_out = _gray(frame_out)

        cropped_in = _crop(gray_in, self.roi)
        if cropped_in is not None:
            self.pore_in.append(band_energy(cropped_in, PORE_BAND_LO, PORE_BAND_HI))
        cropped_out = _crop(gray_out, self.roi)
        if cropped_out is not None:
            self.pore_out.append(band_energy(cropped_out, PORE_BAND_LO, PORE_BAND_HI))

        lap_in = self._ndimage.laplace(gray_in)
        lap_out = self._ndimage.laplace(gray_out)
        if self._prev_in is not None:
            self.rgb_in.append(float(np.abs(frame_in - self._prev_in).mean()))
            self.rgb_out.append(float(np.abs(frame_out - self._prev_out).mean()))
            self.hf_in.append(float(np.abs(lap_in - self._prev_lap_in).mean()))
            self.hf_out.append(float(np.abs(lap_out - self._prev_lap_out).mean()))

        self._prev_in = frame_in.copy()
        self._prev_out = frame_out.copy()
        self._prev_lap_in = lap_in
        self._prev_lap_out = lap_out
        self.frames += 1

    @staticmethod
    def _pct(before: float, after: float) -> float:
        if abs(before) < 1e-12:
            return 0.0
        return (after - before) / before * 100.0

    def result(self) -> dict:
        pore_in = float(np.mean(self.pore_in)) if self.pore_in else 0.0
        pore_out = float(np.mean(self.pore_out)) if self.pore_out else 0.0
        rgb_in = float(np.mean(self.rgb_in)) if self.rgb_in else 0.0
        rgb_out = float(np.mean(self.rgb_out)) if self.rgb_out else 0.0
        hf_in = float(np.mean(self.hf_in)) if self.hf_in else 0.0
        hf_out = float(np.mean(self.hf_out)) if self.hf_out else 0.0
        return {
            "pore_band_energy": {
                "input": pore_in,
                "output": pore_out,
                "delta_pct": self._pct(pore_in, pore_out),
                "band_px": [PORE_BAND_LO, PORE_BAND_HI],
                "frames_measured": len(self.pore_out),
            },
            "temporal_rgb_delta": {
                "input": rgb_in,
                "output": rgb_out,
                "delta_pct": self._pct(rgb_in, rgb_out),
                "frames_measured": len(self.rgb_out),
            },
            "temporal_hf_delta": {
                "input": hf_in,
                "output": hf_out,
                "delta_pct": self._pct(hf_in, hf_out),
                "frames_measured": len(self.hf_out),
            },
            "frames_compared": self.frames,
        }


def compare_runs(
    source_frames: list[np.ndarray],
    output_frames: list[np.ndarray],
    roi: tuple[float, float, float, float] | None = None,
) -> dict:
    """Deltas of the tracked metrics, input versus output.

    Every number here is computed from the frames that were actually
    processed - nothing is estimated.
    """
    accumulator = RunMetrics(roi)
    for frame_in, frame_out in zip(source_frames, output_frames):
        accumulator.update(frame_in, frame_out)
    return accumulator.result()
