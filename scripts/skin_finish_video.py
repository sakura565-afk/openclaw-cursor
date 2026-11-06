# -*- coding: utf-8 -*-
"""Apply the still-pipeline skin finish to every frame of a rendered clip.

Why: the video model removes the micro-texture that the still pipeline built
(measured on our own clip: still 10.45 -> FreeVideo 4.65 micro-texture). The
texture lives entirely in the post chain (grade + BC pores + v2), so it has to be
re-applied to the VIDEO frames in pixel space - anything injected before the VAE
is destroyed by it.

Per frame:
  1. person-aware skin mask (scripts/skin_mask_v2.py) with a chromatic fallback
  2. the grade from scripts/skin_grade.py (colour + micro-contrast)
  3. BC_SkinTexture with a FIXED seed (a per-frame varying seed makes the pore
     field crawl: measured injected-layer delta 0.52 vs 0.41 for a fixed seed)

The frames are written as PNG and re-assembled at the source frame rate with the
original audio muxed back.
"""
from __future__ import annotations

import argparse
import glob
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
# NB: os.path.join("E:", "x") yields "E:x" - a drive-relative path. Keep the
# drive letter and the rest of the path together as one string.
# The ComfyUI install dir is machine-specific, so it comes from the environment.
# NB: os.path.join("E:", "x") yields "E:x" - a drive-relative path - so the
# drive letter and the rest of the path must stay together in one string.
PROD_DIR = os.environ.get(
    "COMFYUI_PROD_DIR",
    "E:" + chr(92) + "ComfyUI_windows_portable_nvidia_cu126" + chr(92) + "ComfyUI_windows_portable",
)
PROD_IN = os.path.join(PROD_DIR, "ComfyUI", "input")
PROD_OUT = os.path.join(PROD_DIR, "ComfyUI", "output")
BASE = "http://127.0.0.1:8188"


def api(path, payload=None, timeout=240):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def bc_pores(img_path, mask_path, seed, texture, detail, tag):
    """Run BC_SkinTexture once on one frame; return the output path."""
    in_img = os.path.join(PROD_IN, f"sf_{tag}_in.png")
    in_mask = os.path.join(PROD_IN, f"sf_{tag}_mask.png")
    shutil.copy2(img_path, in_img)
    shutil.copy2(mask_path, in_mask)
    nodes = {
        "1": {"class_type": "LoadImage", "inputs": {"image": os.path.basename(in_img)}},
        "2": {"class_type": "LoadImage", "inputs": {"image": os.path.basename(in_mask)}},
        "3": {"class_type": "ImageToMask", "inputs": {"image": ["2", 0], "channel": "red"}},
        "4": {"class_type": "BC_SkinTexture",
              "inputs": {"image": ["1", 0], "mask": ["3", 0],
                         "texture": texture, "detail": detail, "pore_scale": 1.0,
                         "feather": 8, "seed": seed, "threshold": 0.5,
                         "sam3_model": "sam3.1_multiplex_fp16.safetensors"}},
        "5": {"class_type": "SaveImage",
              "inputs": {"images": ["4", 0], "filename_prefix": f"sf_{tag}"}},
    }
    try:
        resp = api("/prompt", {"prompt": nodes, "client_id": "skinfinish"})
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
                    return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=24.0)
    ap.add_argument("--texture", type=float, default=0.10)
    ap.add_argument("--detail", type=float, default=0.45)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--grade", action="store_true", help="also run skin_grade.py per frame")
    ap.add_argument("--grade-micro", type=float, default=0.8,
                    help="skin_grade micro-contrast. On VIDEO frames keep it at 0: the "
                         "unsharp amplifies the existing frame-to-frame differences and "
                         "raises the temporal delta by ~25 percent (measured), which "
                         "reads as flicker. Colour-only grading is temporally stable.")
    ap.add_argument("--grade-warmth", type=float, default=1.15)
    ap.add_argument("--grade-sat", type=float, default=0.30)
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--no-person-prior", action="store_true",
                    help="skip the BiRefNet person prior (chromatic mask only)")
    ap.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    args = ap.parse_args()

    work = os.path.join(SCRATCH, "skinfinish_work")
    frames_dir = os.path.join(work, "frames")
    out_dir = os.path.join(work, "out")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(frames_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", args.video,
                    "-fps_mode", "passthrough", os.path.join(frames_dir, "f_%05d.png")],
                   check=True, timeout=900)
    frames = sorted(glob.glob(os.path.join(frames_dir, "f_*.png")))
    if args.max_frames:
        frames = frames[: args.max_frames]
    print(f"кадров: {len(frames)}", flush=True)

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(repo, "scripts"))
    import numpy as np
    from PIL import Image
    try:
        from skin_mask_v2 import skin_mask_v2
    except Exception:
        skin_mask_v2 = None
    from skin_detail_shade import skin_mask

    ok = 0
    for i, fr in enumerate(frames):
        pil = Image.open(fr).convert("RGB")
        if skin_mask_v2 is not None:
            m = np.asarray(skin_mask_v2(
                pil,
                use_comfy=not args.no_person_prior,
                comfy_url=args.comfy_url,
            ))
        else:
            arr = np.asarray(pil).astype(np.float32) / 255
            m = (skin_mask(arr) > 0.45).astype(np.float32)
        mp = os.path.join(work, f"m_{i:05d}.png")
        Image.fromarray((np.clip(m, 0, 1) * 255).astype(np.uint8)).save(mp)

        src = fr
        if args.grade:
            gp = os.path.join(work, f"g_{i:05d}.png")
            r = subprocess.run(
                [sys.executable, os.path.join(repo, "scripts", "skin_grade.py"),
                 "--src", fr, "--out", gp,
                 "--warmth", str(args.grade_warmth), "--sat", str(args.grade_sat),
                 "--micro", str(args.grade_micro),
                 "--face-a", fr, "--face-b", fr],
                capture_output=True, text=True, timeout=300)
            if os.path.exists(gp):
                src = gp

        res = bc_pores(src, mp, args.seed, args.texture, args.detail, f"f{i:05d}")
        # The output sequence has to be CONTIGUOUS. ffmpeg's image2 pattern
        # stops at the first missing index, so a single frame that failed to
        # land silently truncated the whole video. Number by position, not by
        # the source file name.
        dst = os.path.join(out_dir, f"f_{i + 1:05d}.png")
        if res:
            shutil.copy2(res, dst)
            ok += 1
        else:
            shutil.copy2(src, dst)
        if (i + 1) % 25 == 0 or i == 0:
            print(f"  {i + 1}/{len(frames)} (поры {ok})", flush=True)

    if ok == 0:
        print("ни один кадр не обработан - копирую исходник", flush=True)
        for i, fr in enumerate(frames):
            shutil.copy2(fr, os.path.join(out_dir, f"f_{i + 1:05d}.png"))

    # The source audio has to be muxed back explicitly: the PNG sequence carries
    # no audio, and "-c:a copy" without an audio input silently produces a file
    # with no audio track (which then breaks the RIFE step that maps 1:a:0).
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-framerate", str(args.fps), "-start_number", "1",
           "-i", os.path.join(out_dir, "f_%05d.png"),
           "-i", args.video,
           "-map", "0:v:0", "-map", "1:a:0?",
           "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
    # The frame count is pinned: with -shortest the length came from the
    # audio stream, and an AAC stream is never exactly as long as the video -
    # the 7.29 s clip came out one frame short and the picture finished
    # before the music did. The audio is copied, not re-encoded.
           "-c:a", "copy", "-frames:v", str(len(frames)), args.out]
    subprocess.run(cmd, check=True, timeout=1800)
    print(f"готово: {args.out} ({os.path.getsize(args.out) // 1024} КБ, "
          f"поры на {ok}/{len(frames)} кадрах)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
