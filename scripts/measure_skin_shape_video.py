"""Measure the skin of a video with the validated shape feature.

`component_density_per_10k` is the feature that separated pores from grain on
the human-labelled blind sheet (20/20), so it is the honest measuring stick
for "does this clip have pores or is it airbrushed".
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from skin_metric_shape.shape import measure_shape  # noqa: E402


def face_crops(video: str, box: tuple[int, int, int, int], frames: list[int],
               workdir: Path) -> list[tuple[str, dict]]:
    x, y, w, h = box
    workdir.mkdir(parents=True, exist_ok=True)
    for f in workdir.glob("*.png"):
        f.unlink()
    sel = "+".join(f"eq(n\\,{n})" for n in frames)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", video,
                    "-vf", f"select='{sel}',crop={w}:{h}:{x}:{y}",
                    "-fps_mode", "passthrough", str(workdir / "c%02d.png")],
                   capture_output=True, timeout=900)
    out = []
    for f in sorted(workdir.glob("*.png")):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(f),
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True, timeout=120).stdout
        if not raw:
            continue
        side = int(round(len(raw) ** 0.5))
        img = np.frombuffer(raw, dtype=np.uint8)[:side * side].reshape(side, -1)
        out.append((f.name, measure_shape(img)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="+")
    ap.add_argument("--box", default="380,300,120,120", help="x,y,w,h")
    ap.add_argument("--frames", default="10,40,70,100,130,160")
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()

    box = tuple(int(v) for v in a.box.split(","))
    frames = [int(v) for v in a.frames.split(",")]
    base = Path(a.work_dir) if a.work_dir else Path(
        os.environ.get("TEMP", "/tmp")) / "shapeprobe"
    base.mkdir(parents=True, exist_ok=True)

    report = {}
    for vid in a.videos:
        tag = Path(vid).stem
        rows = face_crops(vid, box, frames, base / tag)
        if not rows:
            print(f"  {tag}: не удалось снять кропы")
            continue
        dens = [r[1].get("component_density_per_10k", 0.0) for r in rows]
        rms = [r[1].get("pore_rms", 0.0) for r in rows]
        report[vid] = {
            "component_density_per_10k_mean": round(float(np.mean(dens)), 3),
            "component_density_per_10k_min": round(float(min(dens)), 3),
            "component_density_per_10k_max": round(float(max(dens)), 3),
            "pore_rms_mean": round(float(np.mean(rms)), 4),
            "crops": len(rows),
        }
        print(f"  {tag:22s} density/10k {np.mean(dens):8.2f} "
              f"(min {min(dens):.1f} max {max(dens):.1f}) | pore_rms {np.mean(rms):.3f} | n={len(rows)}")

    if a.json_out:
        Path(a.json_out).write_text(
            __import__("json").dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
