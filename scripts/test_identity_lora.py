# -*- coding: utf-8 -*-
"""A/B/C test for a trained identity LoRA on Qwen-Image 2.1.

The question this answers: does the LoRA actually encode the identity, or is
it just another set of weights that changes the picture?

Method:
  * the source frame is one of the VALIDATION images - the LoRA never saw it,
    so it cannot be memorising it
  * three renders of the same frame, same seed, same prompt:
      A  no LoRA
      B  LoRA at full strength
      C  LoRA at a lower strength
  * the prompt carries the trigger word, which is the only way the trained
    identity is reachable

Everything else - the model, the VAE, the text encoder, the sampler - is
identical, so a difference in the output is attributable to the LoRA.

Usage:
  python scripts/test_identity_lora.py --lora katya_identity_qwen21.safetensors \
      --source <validation image> --trigger katya_person --out-dir <dir>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.request
import uuid

SCRATCH = os.environ.get(
    "HERMES_SCRATCH_DIR",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "cache", "scratch"))
BASE = os.environ.get("COMFYUI_PROD", "http://127.0.0.1:8188")


def _resolve_prod_dir() -> str:
    env = os.environ.get("COMFYUI_PROD_DIR")
    if env:
        return env
    import glob
    for letter in "CDEFGH":
        for hit in glob.glob(f"{letter}:/ComfyUI_windows_portable*/ComfyUI_windows_portable"):
            return hit
    raise SystemExit("COMFYUI_PROD_DIR is not set and no ComfyUI portable was found")


PROD_DIR = _resolve_prod_dir()
PROD_IN = os.path.join(PROD_DIR, "ComfyUI", "input")
PROD_OUT = os.path.join(PROD_DIR, "ComfyUI", "output")

UNET = "qwen_image_2.1_int8_convrot.safetensors"
VAE = "qwen_image_2.1_vae_bf16.safetensors"
CLIP = "qwen3vl_8b_int8_convrot.safetensors"
NEG = ("blurry, low quality, deformed, distorted, extra limbs, extra fingers, "
       "mutated hands, watermark, text, cartoon, 3d render, cgi")


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        r = getattr(stream, "reconfigure", None)
        if r is None:
            continue
        try:
            r(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def graph(image_name: str, prompt: str, seed: int, denoise: float, steps: int,
          lora: str, lora_strength: float, width: int, height: int) -> dict:
    g: dict = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "5": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "7": {"class_type": "TextEncodeQwenImage21",
              "inputs": {"clip": ["2", 0], "prompt": prompt, "negative_prompt": NEG,
                         "vae": ["3", 0], "resolution": 0,
                         "images": {"image_1": ["5", 0]}}},
        "8": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["3", 0]}},
        "12": {"class_type": "KSampler",
               "inputs": {"model": ["1", 0], "positive": ["7", 0], "negative": ["7", 0],
                          "latent_image": ["8", 0], "seed": seed, "steps": steps,
                          "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple",
                          "denoise": denoise}},
        "9": {"class_type": "VAEDecode",
              "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": "idtest"}},
    }
    if lora and lora_strength > 0:
        g["4"] = {"class_type": "LoraLoaderModelOnly",
                  "inputs": {"model": ["1", 0], "lora_name": lora,
                             "strength_model": lora_strength, "strength_clip": 0.0}}
        g["12"]["inputs"]["model"] = ["4", 0]
    return g


def render(g: dict, timeout: int = 1200) -> str:
    req = urllib.request.Request(
        BASE + "/prompt", data=json.dumps({"prompt": g}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        pid = json.loads(r.read().decode())["prompt_id"]
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
    raise TimeoutError(pid)


def main() -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--lora", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--trigger", required=True)
    ap.add_argument("--out-dir", default=os.path.join(SCRATCH, "lora_test"))
    ap.add_argument("--denoise", type=float, default=0.60)
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--strengths", type=float, nargs="*", default=[1.0, 0.6])
    ap.add_argument("--extra-prompt", default="")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    name = os.path.basename(args.source)
    shutil.copy2(args.source, os.path.join(PROD_IN, name))

    prompt = f"{args.trigger}, a photo of a woman, natural skin texture with visible pores, realistic lighting"
    if args.extra_prompt:
        prompt += ", " + args.extra_prompt

    variants = [("A_base", 0.0)] + [(f"B_lora{int(s*100)}", s) for s in args.strengths]
    results = []
    for label, strength in variants:
        g = graph(name, prompt, args.seed, args.denoise, args.steps,
                  args.lora, strength, 0, 0)
        produced = render(g)
        if not produced:
            print(f"[{label}] пустой результат", flush=True)
            continue
        dst = os.path.join(args.out_dir, f"{label}.png")
        shutil.copy2(produced, dst)
        results.append({"label": label, "strength": strength, "path": dst})
        print(f"[{label}] strength={strength} -> {dst}", flush=True)

    src_dst = os.path.join(args.out_dir, "source.png")
    shutil.copy2(args.source, src_dst)
    results.append({"label": "source", "strength": None, "path": src_dst})
    with open(os.path.join(args.out_dir, "result.json"), "w", encoding="utf-8") as fh:
        json.dump({"prompt": prompt, "seed": args.seed, "denoise": args.denoise,
                   "steps": args.steps, "results": results}, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"out_dir": args.out_dir, "variants": len(results)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
