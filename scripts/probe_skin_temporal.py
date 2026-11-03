# -*- coding: utf-8 -*-
"""Measure pore detail and temporal flicker after per-frame pixel models."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_skin_detail import crop_metric, laplacian, run_model


def load_gray(path: Path, box: tuple[int, int, int, int]) -> np.ndarray:
    rgb = np.asarray(Image.open(path).convert("RGB").crop(box), dtype=np.float32)
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def sequence_metrics(files: list[Path], box: tuple[int, int, int, int]) -> dict[str, float]:
    grays = [load_gray(path, box) for path in files]
    rgb = [np.asarray(Image.open(path).convert("RGB").crop(box), dtype=np.float32) for path in files]
    delta = float(np.mean([np.abs(rgb[i + 1] - rgb[i]).mean() for i in range(len(rgb) - 1)]))
    hf_delta = float(np.mean([np.abs(laplacian(grays[i + 1]) - laplacian(grays[i])).mean() for i in range(len(grays) - 1)]))
    micro = float(np.mean([crop_metric(path, box)["microtexture"] for path in files]))
    return {"microtexture": round(micro, 4), "temporal_delta": round(delta, 4), "hf_temporal_delta": round(hf_delta, 4)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--models", nargs="+", default=["1x-ITF-SkinDiffDetail-Lite-v1.pth"])
    ap.add_argument("--count", type=int, default=24)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--box", type=int, nargs=4, default=[350, 783, 600, 912])
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    source_dir = args.out_dir / "source_frames"
    source_dir.mkdir(exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(args.video),
        "-vf", f"select='between(n,{args.start},{args.start + args.count - 1})'",
        "-fps_mode", "passthrough", str(source_dir / "f_%04d.png"),
    ], check=True, timeout=180)
    sources = sorted(source_dir.glob("f_*.png"))
    if len(sources) < 2:
        raise RuntimeError("fewer than two frames extracted")
    box = tuple(args.box)
    report = {"video": str(args.video), "frames": len(sources), "box": box, "results": {}}
    base = sequence_metrics(sources, box)
    report["results"]["source"] = base
    print("source", json.dumps(base), flush=True)
    for model in args.models:
        stem = Path(model).stem.replace("-", "_")
        out_dir = args.out_dir / stem
        out_dir.mkdir(exist_ok=True)
        outputs = []
        for index, source in enumerate(sources):
            out = out_dir / source.name
            run_model(source, model, out)
            outputs.append(out)
            if (index + 1) % 6 == 0:
                print(model, f"{index + 1}/{len(sources)}", flush=True)
        metrics = sequence_metrics(outputs, box)
        metrics["temporal_delta_ratio"] = round(metrics["temporal_delta"] / base["temporal_delta"], 4)
        metrics["hf_temporal_delta_ratio"] = round(metrics["hf_temporal_delta"] / base["hf_temporal_delta"], 4)
        video = args.out_dir / f"{args.video.stem}_{stem}.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-framerate", "24", "-i", str(out_dir / "f_%04d.png"),
            "-i", str(args.video), "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "libx264",
            "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", str(video),
        ], check=True, timeout=300)
        metrics["video"] = str(video)
        report["results"][model] = metrics
        print(model, json.dumps(metrics), flush=True)
    report_path = args.out_dir / "temporal_metrics.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
