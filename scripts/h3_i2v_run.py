# -*- coding: utf-8 -*-
"""H3 I2V runner (PROD ComfyUI :8188) with exact LoRA names.

IMPORTANT INVARIANT: a LoraLoader exposes CLIP as output **1**, not 0 - using
[prev_c, 0] for the LoRA CLIP output makes ComfyUI answer HTTP 400.

Usage:
python h3_run.py --image NAME.jpg --prompt-file p.txt --loras-json '[{"n":"...","sm":0.85,"sc":0.8}]'
   --width 1056 --height 704 --length 175 --seed 1 --prefix pfx --audio a.wav --output out.mp4
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = os.environ.get("COMFYUI_PROD_URL", "http://127.0.0.1:8188")
COMFYUI_INPUT_DIR = os.environ.get("COMFYUI_INPUT_DIR", r"E:\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable\ComfyUI\input")
COMFYUI_OUTPUT_DIR = os.environ.get("COMFYUI_OUTPUT_DIR", r"E:\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable\ComfyUI\output")
FFMPEG = os.environ.get("FFMPEG_BIN", r"C:\Users\user\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.1-full_build\bin\ffmpeg.exe")


def _scaled_image(nodes: dict, nid: int, image_name: str, source: list) -> tuple[str, int]:
    """Add a LoadImage + ImageScaleToTotalPixels pair, return (scale node id, next nid)."""
    nodes[str(nid)] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    load_id = str(nid)
    nid += 1
    nodes[str(nid)] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
        "image": [load_id, 0], "upscale_method": "lanczos", "megapixels": 0.73, "resolution_steps": 1}}
    return str(nid), nid + 1


def build_workflow(a):
    loras = json.loads(a.loras_json)
    nodes = {
        "1": {"class_type": "LoadImage", "inputs": {"image": a.image}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors", "type": "minimax"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
    }
    prev_m, prev_c = "2", "3"
    nid = 5
    for lo in loras:
        nodes[str(nid)] = {"class_type": "LoraLoader", "inputs": {
            "model": [prev_m, 0], "clip": [prev_c, 1] if prev_c != "3" else [prev_c, 0],
            "lora_name": lo["n"], "strength_model": lo["sm"], "strength_clip": lo.get("sc", lo["sm"])}}
        prev_m = prev_c = str(nid)
        nid += 1
    nodes[str(nid)] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
        "image": ["1", 0], "upscale_method": "lanczos", "megapixels": 0.73, "resolution_steps": 1}}
    scale_id = str(nid)
    nid += 1
    h3_inputs = {
        "clip": [prev_c, 1] if prev_c != "3" else [prev_c, 0], "vae": ["4", 0], "first_frame": [scale_id, 0],
        "prompt": a.prompt,
        "negative_prompt": a.negative or ("different face, extra person, blurry, distorted, morphed, "
                                          "plastic skin, anime, text, identity drift, clothed, "
                                          "extra limbs, duplicated limbs, distorted hands, "
                                          "man sitting behind her, gap between the bodies"),
        "width": a.width, "height": a.height, "length": a.length}
    # The 2026-10 core can pin the LAST frame as well (MiniMaxH3ImageToVideo.last_frame).
    if getattr(a, "last_frame", ""):
        last_scale, nid = _scaled_image(nodes, nid, a.last_frame, ["1", 0])
        h3_inputs["last_frame"] = [last_scale, 0]
    nodes[str(nid)] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": h3_inputs}
    vid = str(nid)
    nid += 1
    positive = [vid, 0]
    # A guide image can be pinned to a specific frame of the clip (MiniMaxH3AddGuide).
    if getattr(a, "guide_image", ""):
        guide_scale, nid = _scaled_image(nodes, nid, a.guide_image, ["1", 0])
        nodes[str(nid)] = {"class_type": "MiniMaxH3AddGuide", "inputs": {
            "positive": [vid, 0], "latent": [vid, 1],
            "frame_idx": getattr(a, "guide_frame", 0),
            "image": [guide_scale, 0], "vae": ["4", 0]}}
        positive = [str(nid), 0]
        nid += 1
    nodes[str(nid)] = {"class_type": "KSampler", "inputs": {
        "model": [prev_m, 0], "positive": positive, "negative": [vid, 0], "latent_image": [vid, 1],
        "seed": a.seed, "steps": a.steps, "cfg": 1.0, "sampler_name": "euler",
        "scheduler": "simple", "denoise": 1.0}}
    samp = str(nid)
    nid += 1
    nodes[str(nid)] = {"class_type": "VAEDecode", "inputs": {"samples": [samp, 0], "vae": ["4", 0]}}
    dec = str(nid)
    nid += 1
    nodes[str(nid)] = {"class_type": "CreateVideo", "inputs": {"images": [dec, 0], "fps": 24.0}}
    cv = str(nid)
    nid += 1
    nodes[str(nid)] = {"class_type": "SaveVideo", "inputs": {
        "video": [cv, 0], "filename_prefix": a.prefix, "format": "auto", "codec": "h264"}}
    return nodes


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--image", required=True)
    p.add_argument("--last-frame", default="",
                   help="image in ComfyUI/input pinned as the LAST frame of the clip")
    p.add_argument("--guide-image", default="",
                   help="image in ComfyUI/input added as a guide reference (MiniMaxH3AddGuide)")
    p.add_argument("--guide-frame", type=int, default=0,
                   help="frame index the guide image is pinned to")
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--prompt-prefix", default="")
    p.add_argument("--loras-json", default="[]")
    p.add_argument("--negative", default="")
    p.add_argument("--width", type=int, default=1056)
    p.add_argument("--height", type=int, default=704)
    p.add_argument("--length", type=int, default=175)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--steps", type=int, default=14)
    p.add_argument("--prefix", default="h3run")
    p.add_argument("--audio", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--wait", type=int, default=7200)
    a = p.parse_args()
    a.prompt = a.prompt_prefix + "\n" + open(a.prompt_file, encoding="utf-8").read()

    req = urllib.request.Request(BASE + "/prompt", data=json.dumps({"prompt": build_workflow(a)}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    pid = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())["prompt_id"]
    print("prompt_id", pid, flush=True)

    t0 = time.time()
    last = -1
    while time.time() - t0 < a.wait:
        h = json.loads(urllib.request.urlopen(BASE + "/history/" + pid, timeout=30).read().decode())
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                print("ERROR:", str(st.get("messages"))[:600], flush=True)
                return 1
            for _, o in h[pid].get("outputs", {}).items():
                for v in o.get("videos", []) + o.get("images", []):
                    fn = v.get("filename", "")
                    sub = v.get("subfolder", "")
                    src = os.path.join(COMFYUI_OUTPUT_DIR, sub, fn)
                    if not src.lower().endswith(".mp4"):
                        cand = [x for x in os.listdir(os.path.join(COMFYUI_OUTPUT_DIR, sub)) if x.startswith(fn.split(".")[0]) and x.endswith(".mp4")]
                        if not cand:
                            continue
                        src = os.path.join(COMFYUI_OUTPUT_DIR, sub, cand[0])
                    print("видео:", src, flush=True)
                    cmd = [FFMPEG, "-y", "-stream_loop", "-1", "-i", src, "-i", a.audio,
                           "-t", "7.3", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                           "-map", "0:v:0", "-map", "1:a:0", "-shortest", a.output]
                    subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                    print("готово:", a.output, round(os.path.getsize(a.output) / 1e6, 2), "МБ", flush=True)
                    return 0
            return 0
        el = int(time.time() - t0)
        if el // 60 != last:
            last = el // 60
            q = json.loads(urllib.request.urlopen(BASE + "/queue", timeout=25).read().decode())
            print("[%d мин] running=%d" % (el // 60, len(q.get("queue_running", []))), flush=True)
        time.sleep(15)
    print("таймаут")
    return 2


if __name__ == "__main__":
    sys.exit(main())
