# -*- coding: utf-8 -*-
"""Un-dress a whole video frame by frame with Qwen-Image-Edit, then rebuild it.

WHY A BATCH RUNNER
------------------
Running the single-image script per frame pays the interpreter start-up and the
ComfyUI queue round-trip every time, which dominates the cost of a small edit.
This runner keeps one process alive, submits each frame as soon as the previous
one comes back, and is RESUMABLE: it writes one PNG per frame and skips the
ones that already exist, so an interrupted run continues where it stopped
instead of starting over.

The frame rate is a deliberate knob. A 40 s clip at the source 30 fps is ~1200
edits - far too many. The runner samples the source at `--fps` and the result
is interpolated back up afterwards, which is where the quality of the motion
actually comes from.

Usage:
  # extract, edit every frame, rebuild
  python scripts/undress_video.py --video in.mp4 --out out.mp4 \
      --prompt-file p.txt --fps 12 --denoise 0.92 --size 448 784

  # only the edit stage (useful to resume)
  python scripts/undress_video.py --video in.mp4 --out out.mp4 \
      --prompt-file p.txt --fps 12 --stage frames

  # rebuild from an existing frame sequence
  python scripts/undress_video.py --video in.mp4 --out out.mp4 --stage assemble
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
import uuid

SCRATCH = os.environ.get(
    "HERMES_SCRATCH_DIR",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "cache", "scratch"))
BASE = os.environ.get("COMFYUI_PROD", "http://127.0.0.1:8188")
# Machine-specific by nature: the ComfyUI install directory. Both are
# overridable from the environment for another host, and the pair is recorded
# in the path guard's ALLOWLIST with the reason.
PROD_IN = os.environ.get(
    "COMFYUI_PROD_INPUT",
    r"E:\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable\ComfyUI\input")
PROD_OUT = os.environ.get(
    "COMFYUI_PROD_OUTPUT",
    r"E:\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable\ComfyUI\output")
NEG = ("blurry, low quality, deformed, distorted, extra limbs, extra fingers, "
       "mutated hands, malformed anatomy, watermark, text, cartoon, 3d render, "
       "cgi, painting, illustration")


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        r = getattr(stream, "reconfigure", None)
        if r is None:
            continue
        try:
            r(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def build_graph(image_name: str, positive: str, seed: int, denoise: float,
                steps: int, size: tuple, cfg: float,
                model: str, clip: str, vae: str, lora: str, lora_strength: float) -> dict:
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": model, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": clip, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["1", 0], "lora_name": lora,
                         "strength_model": lora_strength, "strength_clip": 0.0}},
        "5": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "6": {"class_type": "QwenImageIntegratedKSampler",
              "inputs": {"model": ["4", 0], "clip": ["2", 0], "vae": ["3", 0],
                         "image1": ["5", 0],
                         "positive_prompt": positive, "negative_prompt": NEG,
                         "generation_mode": "图生图 image-to-image", "batch_size": 1,
                         "width": size[0], "height": size[1], "seed": seed,
                         "steps": steps, "cfg": cfg,
                         "sampler_name": "euler", "scheduler": "simple",
                         "denoise": denoise, "output_filename_prefix": "undress_"}},
        "7": {"class_type": "SaveImage",
              "inputs": {"images": ["6", 0], "filename_prefix": "undress_"}},
    }


def submit(graph: dict) -> str:
    req = urllib.request.Request(
        BASE + "/prompt", data=json.dumps({"prompt": graph}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode())["prompt_id"]


def wait_output(pid: str, timeout: int = 900) -> str:
    """Return the path of the saved output image, or ''."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"{BASE}/history/{pid}", timeout=60) as r:
                h = json.loads(r.read().decode())
        except Exception:
            time.sleep(3)
            continue
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                raise RuntimeError(json.dumps(st.get("messages"), ensure_ascii=False)[:400])
            for _, o in h[pid].get("outputs", {}).items():
                for im in o.get("images", []):
                    p = os.path.join(PROD_OUT, im.get("subfolder", ""), im["filename"])
                    if not p.lower().endswith(".png"):
                        p += ".png"
                    return p
            return ""
        time.sleep(2)
    raise TimeoutError(f"{pid} не завершился за {timeout}s")


def extract_frames(video: str, out_dir: str, fps: float, size: tuple) -> list:
    os.makedirs(out_dir, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", video,
                    "-vf", f"fps={fps},scale={size[0]}:{size[1]}:flags=lanczos",
                    "-fps_mode", "passthrough", os.path.join(out_dir, "f_%05d.png")],
                   check=True, timeout=1800)
    return sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir)
                  if f.endswith(".png"))


def main() -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--fps", type=float, default=12.0)
    ap.add_argument("--denoise", type=float, default=0.92)
    ap.add_argument("--steps", type=int, default=24)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=88231)
    ap.add_argument("--size", type=int, nargs=2, default=(448, 784))
    ap.add_argument("--model", default="qwen_image_edit_2511_fp8_e4m3fn.safetensors")
    ap.add_argument("--clip", default="qwen_2.5_vl_7b_fp8_scaled.safetensors")
    ap.add_argument("--vae", default="qwen_image_vae_2d.safetensors")
    ap.add_argument("--lora", default="qwen_image_edit_plus_nsfw_lora_safetensors.safetensors")
    ap.add_argument("--lora-strength", type=float, default=1.0)
    ap.add_argument("--stage", default="all", choices=["all", "frames", "assemble"])
    ap.add_argument("--work", default=os.path.join(SCRATCH, "undress_work"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    size = tuple(args.size)
    prompt = open(args.prompt_file, encoding="utf-8").read().strip()
    src_dir = os.path.join(args.work, "src")
    out_dir = os.path.join(args.work, "edited")
    os.makedirs(out_dir, exist_ok=True)

    if args.stage in ("all", "frames"):
        frames = extract_frames(args.video, src_dir, args.fps, size)
        print(f"кадров извлечено: {len(frames)} ({args.fps} fps, {size[0]}x{size[1]})",
              flush=True)
        if args.limit:
            frames = frames[: args.limit]
        done = 0
        t0 = time.time()
        for i, src in enumerate(frames):
            dst = os.path.join(out_dir, os.path.basename(src))
            if os.path.exists(dst):
                done += 1
                continue
            in_name = os.path.basename(src)
            shutil.copy2(src, os.path.join(PROD_IN, in_name))
            graph = build_graph(in_name, prompt, args.seed, args.denoise, args.steps,
                                size, args.cfg, args.model, args.clip, args.vae,
                                args.lora, args.lora_strength)
            pid = submit(graph)
            produced = wait_output(pid)
            if not produced:
                print(f"[{i+1}/{len(frames)}] пустой результат, пропуск", flush=True)
                continue
            from PIL import Image
            Image.open(produced).convert("RGB").resize(size, Image.LANCZOS).save(dst)
            done += 1
            if (i + 1) % 10 == 0 or i == 0:
                rate = (time.time() - t0) / max(done, 1)
                left = (len(frames) - i - 1) * rate
                print(f"[{i+1}/{len(frames)}] готово {done} | "
                      f"{rate:.1f} с/кадр | осталось ~{left/60:.0f} мин", flush=True)
        print(f"всего обработано: {done}", flush=True)

    if args.stage in ("all", "assemble"):
        edited = sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir)
                        if f.endswith(".png"))
        if not edited:
            print("нет обработанных кадров", file=sys.stderr)
            return 2
        tmp = os.path.join(args.work, "silent.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(args.fps),
                        "-i", os.path.join(out_dir, "f_%05d.png"),
                        "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", tmp],
                       check=True, timeout=1800)
        # mux the ORIGINAL audio so the timing is unchanged
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", tmp, "-i", args.video,
                        "-map", "0:v:0", "-map", "1:a:0?",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                        args.out], check=True, timeout=900)
        print(json.dumps({"out": args.out,
                          "frames": len(edited),
                          "fps": args.fps,
                          "size": list(size),
                          "bytes": os.path.getsize(args.out)}, ensure_ascii=False),
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
