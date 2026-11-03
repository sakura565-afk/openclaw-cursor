# -*- coding: utf-8 -*-
"""Blend an ITF frame sequence with its source and measure temporal stability."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from probe_skin_temporal import sequence_metrics


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", type=Path, required=True)
    ap.add_argument("--detail-dir", type=Path, required=True)
    ap.add_argument("--source-video", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--alpha", type=float, default=0.4)
    ap.add_argument("--box", type=int, nargs=4, default=[350, 783, 600, 912])
    args = ap.parse_args()
    if not 0.0 <= args.alpha <= 1.0:
        raise ValueError("alpha must be in [0, 1]")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    frame_dir = args.out_dir / "frames"
    frame_dir.mkdir(exist_ok=True)
    sources = sorted(args.source_dir.glob("f_*.png"))
    details = sorted(args.detail_dir.glob("f_*.png"))
    if len(sources) != len(details) or len(sources) < 2:
        raise RuntimeError(f"frame mismatch: source={len(sources)}, detail={len(details)}")
    outputs = []
    for source, detail in zip(sources, details):
        out = frame_dir / source.name
        if not out.exists():
            a = np.asarray(Image.open(source).convert("RGB"), dtype=np.float32)
            b = np.asarray(Image.open(detail).convert("RGB"), dtype=np.float32)
            Image.fromarray(np.clip(a * (1.0 - args.alpha) + b * args.alpha, 0, 255).astype(np.uint8)).save(out)
        outputs.append(out)
    box = tuple(args.box)
    base = sequence_metrics(sources, box)
    full = sequence_metrics(details, box)
    blend = sequence_metrics(outputs, box)
    blend["temporal_delta_ratio"] = round(blend["temporal_delta"] / base["temporal_delta"], 4)
    blend["hf_temporal_delta_ratio"] = round(blend["hf_temporal_delta"] / base["hf_temporal_delta"], 4)
    video = args.out_dir / f"itf_blend_{int(round(args.alpha * 100)):02d}.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-framerate", "24", "-i", str(frame_dir / "f_%04d.png"),
        "-i", str(args.source_video), "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "libx264",
        "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", str(video),
    ], check=True, timeout=300)
    report = {"alpha": args.alpha, "frames": len(outputs), "box": box, "source": base, "itf_full": full, "blend": blend, "video": str(video)}
    path = args.out_dir / "metrics.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
