# -*- coding: utf-8 -*-
"""Masked Qwen-Image-Edit inpaint (PROD :8188) - repair ONE region, keep everything else.

Qwen-Image-Edit at denoise 1.0 regenerates the whole frame, which is why the face and the
hand drift. The standard ComfyUI masked-inpaint path keeps the unmasked pixels from the
source latent (VAEEncodeForInpaint) and re-samples only the masked box at a low denoise,
so the pose, face, body and background stay byte-identical outside the mask.

Graph:
  LoadImage(src) + LoadImage(mask) -> ImageToMask -> VAEEncodeForInpaint -> KSampler
  -> VAEDecode -> SaveImage

The Qwen VAE must be the 2D one (qwen_image_vae_2d.safetensors); the stock ComfyUI Qwen
autoencoder is the 3D/video VAE and raises a size mismatch.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("COMFYUI_PROD_URL", "http://127.0.0.1:8188")
PROD_DIR = os.environ.get(
    "COMFYUI_PROD_DIR", r"E:\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable"
)
PROD_IN = os.path.join(PROD_DIR, "ComfyUI", "input")
PROD_OUT = os.path.join(PROD_DIR, "ComfyUI", "output")
SCRATCH = os.environ.get(
    "HERMES_SCRATCH_DIR",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "cache", "scratch"),
)

UNET = os.environ.get("QWEN_EDIT_MODEL", "qwen_image_edit_2509_fp8_e4m3fn.safetensors")
CLIP = os.environ.get("QWEN_EDIT_CLIP", "qwen_2.5_vl_7b_fp8_scaled.safetensors")
VAE = os.environ.get("QWEN_EDIT_VAE", "qwen_image_vae_2d.safetensors")
LORA = os.environ.get("QWEN_NSFW_LORA", "qwen_image_edit_plus_nsfw_lora_safetensors.safetensors")
LORA_STR = float(os.environ.get("QWEN_NSFW_LORA_STRENGTH", "1.0"))

# ВАЖНО: нельзя писать "detached hand"/"disconnected wrist" - модель тогда УДАЛЯЕТ кисть
# вместо того чтобы её чинить (проверено на маске у талии).
NEG = ("blurry, deformed, extra fingers, fused fingers, missing fingers, extra limbs, "
       "claws, disfigured hand, swollen hand, "
       "text, watermark, cartoon, illustration, smooth skin, waxy skin, plastic skin")


def api(path, payload=None, timeout=180):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def preflight(class_types):
    info = api("/object_info", timeout=120)
    missing = [c for c in class_types if c not in info]
    if missing:
        raise SystemExit(f"НЕТ НОД в ComfyUI: {missing}")


def run(tag, src_name, mask_name, positive, seed, denoise=0.55, steps=24,
        size=(1088, 1620), grow=8):
    preflight(["UNETLoader", "CLIPLoader", "VAELoader", "LoraLoaderModelOnly", "LoadImage",
               "ImageToMask", "VAEEncodeForInpaint", "CLIPTextEncode", "KSampler",
               "VAEDecode", "SaveImage"])
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["1", 0], "lora_name": LORA,
                         "strength_model": LORA_STR, "strength_clip": 0.0}},
        "5": {"class_type": "LoadImage", "inputs": {"image": src_name}},
        "6": {"class_type": "LoadImage", "inputs": {"image": mask_name}},
        "7": {"class_type": "ImageToMask", "inputs": {"image": ["6", 0], "channel": "red"}},
        "8": {"class_type": "VAEEncodeForInpaint",
              "inputs": {"pixels": ["5", 0], "vae": ["3", 0], "mask": ["7", 0], "grow_mask_by": grow}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": positive}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": NEG}},
        "11": {"class_type": "KSampler",
               "inputs": {"model": ["4", 0], "seed": seed, "steps": steps,
                          "cfg": float(os.environ.get("QWEN_EDIT_CFG", "1.0")),
                          "sampler_name": "euler", "scheduler": "simple", "denoise": denoise,
                          "positive": ["9", 0], "negative": ["10", 0], "latent_image": ["8", 0]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": "qfix_" + tag}},
    }
    # Второй LoRA-слой (NSFW). Без него Qwen-Edit "выбеливает" нагую кожу: замер - тёплых
    # пикселей в зоне бедра падает с 82% до 15%, кожа уходит в серый (проверено).
    lora2 = os.environ.get("QWEN_LORA2", "").strip()
    if lora2:
        graph["40"] = {"class_type": "LoraLoaderModelOnly",
                       "inputs": {"model": ["4", 0], "lora_name": lora2,
                                  "strength_model": float(os.environ.get("QWEN_LORA2_STRENGTH", "1.0")),
                                  "strength_clip": 0.0}}
        graph["11"]["inputs"]["model"] = ["40", 0]
    try:
        resp = api("/prompt", {"prompt": graph, "client_id": "qfix"}, timeout=240)
    except urllib.error.HTTPError as e:
        print(f"{tag}: HTTP {e.code} {e.read().decode('utf-8','replace')[:700]}", flush=True)
        return None
    pid = resp.get("prompt_id")
    if not pid:
        print(f"{tag}: нет prompt_id {json.dumps(resp)[:400]}", flush=True)
        return None
    print(f"{tag}: prompt_id={pid} denoise={denoise} steps={steps} seed={seed}", flush=True)
    t0 = time.time()
    while time.time() - t0 < 2400:
        time.sleep(10)
        try:
            h = api(f"/history/{pid}", timeout=60)
        except Exception:
            continue
        e = h.get(pid)
        if not e:
            continue
        if e.get("status", {}).get("status_str") == "error":
            print(f"{tag}: ОШИБКА {json.dumps(e.get('status', {}).get('messages', []))[:500]}", flush=True)
            return None
        for o in e.get("outputs", {}).values():
            for im in o.get("images", []):
                p = os.path.join(PROD_OUT, im.get("subfolder", ""), im["filename"])
                if os.path.exists(p):
                    dst = os.path.join(SCRATCH, f"{tag}.png")
                    from PIL import Image
                    Image.open(p).convert("RGB").resize(size, Image.LANCZOS).save(dst)
                    print(f"{tag}: ГОТОВО за {int(time.time()-t0)} с -> {dst} "
                          f"({os.path.getsize(dst)//1024} КБ)", flush=True)
                    return dst
    print(f"{tag}: ТАЙМАУТ", flush=True)
    return None


if __name__ == "__main__":
    tag = sys.argv[1]
    src = sys.argv[2]
    mask = sys.argv[3]
    positive = open(sys.argv[4], encoding="utf-8").read()
    seed = int(sys.argv[5])
    denoise = float(sys.argv[6]) if len(sys.argv) > 6 else 0.55
    steps = int(sys.argv[7]) if len(sys.argv) > 7 else 24
    w = int(sys.argv[8]) if len(sys.argv) > 8 else 1088
    h = int(sys.argv[9]) if len(sys.argv) > 9 else 1620
    run(tag, src, mask, positive, seed, denoise, steps, (w, h))
