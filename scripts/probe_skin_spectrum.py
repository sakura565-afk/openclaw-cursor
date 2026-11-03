# -*- coding: utf-8 -*-
"""Separate real pore-scale detail from broad specular shading.

The plain mean |laplacian| cannot tell "fine pores" from "glossy highlight
gradients" - both add high-frequency energy. Pores live in a narrow spatial
frequency band (roughly 3-12 px period); specular shading on skin is much
smoother (period > 25 px).

This probe reports, per ROI:
  * ``band_pore``   - energy in the 3-12 px spatial-period band (the pore band)
  * ``band_coarse`` - energy at periods > 25 px (specular / shading)
  * ``pore_ratio``  - band_pore / band_coarse
  * ``hf_ratio``    - share of the laplacian energy that sits in the pore band

A clip that really preserves micro-texture has a high band_pore AND a high
pore_ratio. A clip that merely has glossy skin has a high band_coarse with a
low pore_ratio.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

# normalised (x1, y1, x2, y2) - same boxes as scripts/probe_h3_vs_fv.py
ROIS: dict[str, tuple[float, float, float, float]] = {
    "thigh": (0.170, 0.606, 0.469, 0.821),
    "chest": (0.717, 0.327, 0.916, 0.569),
}


def spectrum(gray: np.ndarray) -> np.ndarray:
    """ radially averaged power spectrum, normalised by the DC energy """
    window = np.hanning(gray.shape[0])[:, None] * np.hanning(gray.shape[1])[None, :]
    fft = np.fft.fftshift(np.fft.fft2(gray * window))
    power = np.abs(fft) ** 2
    rows, cols = power.shape
    cy, cx = rows // 2, cols // 2
    y, x = np.ogrid[:rows, :cols]
    radius = np.sqrt((y - cy) ** 2 + (x - cx) ** 2).astype(int)
    bins = np.bincount(radius.ravel(), weights=power.ravel())
    counts = np.bincount(radius.ravel())
    radial = bins[: max(counts.shape[0], 1)] / np.maximum(counts[: max(counts.shape[0], 1)], 1)
    return radial


def band_energies(gray: np.ndarray, size: int) -> tuple[float, float]:
    """Return (pore band energy, coarse band energy) in spatial-period terms."""
    radial = spectrum(gray)
    freqs = np.fft.fftfreq(size)
    # radial index -> spatial frequency in cycles/px
    k = np.arange(len(radial))
    # frequency of radial bin k (with fftshift, index 0 = DC at centre is len/2)
    half = len(radial) // 2
    f = np.abs(np.fft.fftfreq(len(radial)))
    # map: bin index in the shifted array -> cycles/px
    cycles = np.zeros_like(f)
    cycles[:half] = f[:half][::-1]
    cycles[half:] = f[half:]
    # ignore DC and the very lowest bins
    pore = (cycles >= 1.0 / 12.0) & (cycles < 1.0 / 3.0)
    coarse = (cycles > 0.0) & (cycles < 1.0 / 25.0)
    pore_energy = float(radial[pore].sum())
    coarse_energy = float(radial[coarse].sum())
    return pore_energy, coarse_energy


def analyse(rgb: np.ndarray) -> dict:
    y = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    height, width = y.shape
    out = {}
    for name, (nx1, ny1, nx2, ny2) in ROIS.items():
        x1, x2 = int(nx1 * width), int(nx2 * width)
        y1, y2 = int(ny1 * height), int(ny2 * height)
        sub = y[y1:y2, x1:x2]
        pore, coarse = band_energies(sub, min(sub.shape))
        lap = np.abs(-4 * sub[1:-1, 1:-1] + sub[:-2, 1:-1] + sub[2:, 1:-1]
                     + sub[1:-1, :-2] + sub[1:-1, 2:])
        out[name] = {
            "laplacian": round(float(lap.mean()), 4),
            "band_pore": round(pore, 6),
            "band_coarse": round(coarse, 6),
            "pore_ratio": round(pore / max(coarse, 1e-12), 4),
        }
    return out


def analyse_video(video: Path, count: int, step: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="sp_") as tmp:
        work = Path(tmp)
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video),
               "-vf", f"select='not(mod(n\\,{step}))'",
               "-fps_mode", "passthrough", str(work / "f_%05d.png")]
        subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=True)
        files = sorted(work.glob("f_*.png"))[:count]
        per_roi: dict[str, list[dict]] = {name: [] for name in ROIS}
        for path in files:
            rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
            for name, values in analyse(rgb).items():
                per_roi[name].append(values)
    result = {"video": str(video), "frames": len(files)}
    for name, rows in per_roi.items():
        result[name] = {
            key: round(float(np.mean([r[key] for r in rows])), 6)
            for key in rows[0]
        }
    return result


def analyse_still(image: Path) -> dict:
    rgb = np.asarray(Image.open(image).convert("RGB"), dtype=np.float32)
    result = {"video": str(image), "frames": 1}
    result.update(analyse(rgb))
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--count", type=int, default=24)
    ap.add_argument("--step", type=int, default=4)
    ap.add_argument("--still", action="store_true")
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()
    result = analyse_still(args.video) if args.still else analyse_video(args.video, args.count, args.step)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
