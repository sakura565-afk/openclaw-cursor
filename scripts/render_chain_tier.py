# -*- coding: utf-8 -*-
"""Re-render a segmented action clip at a chosen FreeVideo tier, chained.

The tier decides whether skin micro-texture can be born at all:
  light  = two_pass, upscale_factor 2  -> the first pass runs at 25% of the target
           area (384x576 for 736x1120 = 216 tokens instead of 805), so the pores
           never enter the latent.
  high   = single pass, 16 steps, easycache-av, full resolution.

Each segment's first frame is the previous segment's boundary frame, extracted
from the freshly rendered segment, so the joins stay seamless even when the tier
changes the pixels.

Usage:
  python scripts/render_chain_tier.py --plan plan.json --out-dir <dir>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRATCH = os.environ.get(
    "HERMES_SCRATCH_DIR",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "cache", "scratch"),
)
LAB_INPUT = os.environ.get(
    "LAB_INPUT_DIR",
    "E:" + chr(92) + "ComfyUI_LAB_037" + chr(92) + "ComfyUI_windows_portable"
    + chr(92) + "ComfyUI" + chr(92) + "input",
)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 7200), **kw)
    if r.returncode != 0:
        print("FAIL:", " ".join(str(c) for c in cmd[:6]), "\n", r.stderr[-1500:], flush=True)
        raise SystemExit(1)
    return r.stdout


def ffprobe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
                        "-show_entries", "format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=120)
    return json.loads(r.stdout)


def extract_frame(video, at_seconds, out_png):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at_seconds:.3f}",
                    "-i", str(video), "-frames:v", "1", str(out_png)],
                   check=True, timeout=300)
    if not os.path.exists(out_png):
        raise SystemExit(f"не удалось извлечь кадр из {video}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, help="JSON: list of segments")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--tier", default="high")
    ap.add_argument("--width", type=int, default=736)
    ap.add_argument("--height", type=int, default=1120)
    ap.add_argument("--seed", type=int, default=73571)
    ap.add_argument("--lora", default="", help="FreeVideo LoRA name (file in the LoRA folder)")
    ap.add_argument("--lora-strength", type=float, default=0.0)
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    first_image = plan["first_image"]
    boundary = None  # seconds at which the next segment starts

    for idx, seg in enumerate(plan["segments"]):
        tag = seg["tag"]
        prompt = seg["prompt"]
        seconds = float(seg["seconds"])
        out = out_dir / f"{tag}_raw.mp4"

        # A segment may name its own predecessor clip and the timestamp to take
        # the first frame from; otherwise the previous segment in this plan is used.
        prev = seg.get("prev")
        if prev is None and boundary is not None and idx > 0:
            prev = out_dir / f"{plan['segments'][idx - 1]['tag']}_raw.mp4"
        prev_at = float(seg.get("prev_at", boundary if boundary is not None else 0.0))
        if prev is None:
            src_img = first_image
        else:
            src_img = out_dir / f"{tag}_first.png"
            extract_frame(prev, prev_at, src_img)
            print(f"[{tag}] первый кадр взят из {os.path.basename(prev)} на {prev_at:.2f}s", flush=True)

        # product_shot.py copies the source image into the LAB input dir itself,
        # so it needs a real path here, not just a file name.
        name = f"chain_{tag}.png"
        shutil.copy2(src_img, os.path.join(LAB_INPUT, name))

        cmd = [
            sys.executable, "-u", os.path.join(REPO, "scripts", "product_shot.py"),
            "--image", src_img,
            "--width", str(args.width), "--height", str(args.height),
            "--seconds", str(seconds),
            "--prompt", prompt,
            "--out", str(out),
            "--seed", str(args.seed + idx),
            "--timeout", "9000",
            "--tier", args.tier,
            "--pause-process", "llama-server.exe",
        ]
        if args.lora:
            cmd += ["--lora", args.lora, "--lora-strength", str(args.lora_strength)]
        env = dict(os.environ, PYTHONPATH=REPO)
        print(f"[{tag}] рендер {seconds}s, tier={args.tier} ...", flush=True)
        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10800, env=env, cwd=REPO)
        print(r.stdout[-400:], flush=True)
        if r.returncode != 0:
            print(r.stderr[-1500:], flush=True)
            raise SystemExit(1)
        info = ffprobe(out)
        v = info["streams"][0]
        print(f"[{tag}] готово за {time.time()-t0:.0f}s: {v['width']}x{v['height']} "
              f"{v['nb_frames']} кадров", flush=True)

        boundary = float(seg.get("boundary", seconds))

    print("ЦЕПОЧКА ГОТОВА:", out_dir, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
