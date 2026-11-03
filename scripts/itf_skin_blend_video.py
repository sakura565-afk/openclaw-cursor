# -*- coding: utf-8 -*-
"""Apply the ITF skin-detail pass to every frame of a clip and blend it back.

Measured on this box (probe_skin_20261103, 124-frame temporal A/B):
  ITF 1x on the source frame      microtexture 6.61 -> 13.59 (+105.6 percent)
  ITF 100 percent in the video    HF temporal delta +169 percent -> visible boil
  blend 25 percent                microtexture +23.8 percent, HF delta +42 percent
  blend 40 percent                microtexture +38.6 percent, HF delta +66.4 percent

ITF 1x is the only detail model we found that reads as skin rather than as
speckle (vision 8/10 on a static crop, versus 5.5/10 for SkinContrast and
3.5/10 for RealESRGAN). The blend keeps it from boiling in motion.

Production default: alpha 0.25.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SCRATCH = os.environ.get(
    "HERMES_SCRATCH_DIR",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "cache", "scratch"),
)
PROD_DIR = os.environ.get(
    "COMFYUI_PROD_DIR",
    "E:" + chr(92) + "ComfyUI_windows_portable_nvidia_cu126" + chr(92) + "ComfyUI_windows_portable",
)
PROD_IN = os.path.join(PROD_DIR, "ComfyUI", "input")
PROD_OUT = os.path.join(PROD_DIR, "ComfyUI", "output")
BASE = "http://127.0.0.1:8188"
DEFAULT_MODEL = "1x-ITF-SkinDiffDetail-Lite-v1.pth"


def api(path, payload=None, timeout=300):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def run_model(src_png: Path, model: str, tag: str) -> Path | None:
    """Run one upscale model on one frame through ComfyUI; return the output path."""
    in_name = f"itf_{tag}_in.png"
    shutil.copy2(src_png, os.path.join(PROD_IN, in_name))
    nodes = {
        "1": {"class_type": "LoadImage", "inputs": {"image": in_name}},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
        "3": {"class_type": "ImageUpscaleWithModel",
              "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
        "4": {"class_type": "SaveImage",
              "inputs": {"images": ["3", 0], "filename_prefix": f"itf_{tag}"}},
    }
    try:
        resp = api("/prompt", {"prompt": nodes, "client_id": "itf"})
    except Exception as e:
        print(f"  {tag}: prompt error {str(e)[:90]}", flush=True)
        return None
    pid = resp.get("prompt_id")
    if not pid:
        return None
    t0 = time.time()
    while time.time() - t0 < 300:
        time.sleep(2)
        try:
            h = api(f"/history/{pid}", timeout=60)
        except Exception:
            continue
        e = h.get(pid)
        if not e:
            continue
        if e.get("status", {}).get("status_str") == "error":
            return None
        for o in e.get("outputs", {}).values():
            for im in o.get("images", []):
                p = os.path.join(PROD_OUT, im.get("subfolder", ""), im["filename"])
                if os.path.exists(p):
                    return Path(p)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--alpha", type=float, default=0.25,
                    help="blend weight of the detail pass: out = (1-a)*source + a*detail")
    ap.add_argument("--fps", type=float, default=24.0)
    ap.add_argument("--max-frames", type=int, default=0)
    args = ap.parse_args()

    import numpy as np
    from PIL import Image

    work = Path(os.path.join(SCRATCH, "itf_work"))
    src_dir = work / "src"
    det_dir = work / "det"
    out_dir = work / "out"
    shutil.rmtree(work, ignore_errors=True)
    for d in (src_dir, det_dir, out_dir):
        d.mkdir(parents=True, exist_ok=True)

    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", args.video,
                    "-fps_mode", "passthrough", str(src_dir / "f_%05d.png")],
                   check=True, timeout=900)
    frames = sorted(src_dir.glob("f_*.png"))
    if args.max_frames:
        frames = frames[: args.max_frames]
    print(f"кадров: {len(frames)}  alpha={args.alpha}  model={args.model}", flush=True)

    ok = 0
    for i, src in enumerate(frames):
        det = run_model(src, args.model, f"f{i:05d}")
        dst = out_dir / src.name
        if det is None:
            shutil.copy2(src, dst)
            continue
        a = np.asarray(Image.open(src).convert("RGB")).astype(np.float32)
        b = np.asarray(Image.open(det).convert("RGB")).astype(np.float32)
        if b.shape != a.shape:
            # the model may upscale; take the same-size part and resize the detail
            b = np.asarray(Image.open(det).convert("RGB").resize(
                (a.shape[1], a.shape[0]), Image.LANCZOS)).astype(np.float32)
        merged = (1.0 - args.alpha) * a + args.alpha * b
        Image.fromarray(np.clip(merged, 0, 255).astype(np.uint8)).save(dst)
        ok += 1
        if (i + 1) % 25 == 0 or i == 0:
            print(f"  {i + 1}/{len(frames)} (с блендом {ok})", flush=True)

    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(args.fps),
                    "-i", str(out_dir / "f_%05d.png"), "-i", args.video,
                    "-map", "0:v:0", "-map", "1:a:0?",
                    "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", args.out],
                   check=True, timeout=1800)
    print(f"готово: {args.out} ({os.path.getsize(args.out) // 1024} КБ, "
          f"бленд на {ok}/{len(frames)} кадрах)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
