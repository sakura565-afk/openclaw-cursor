# -*- coding: utf-8 -*-
"""Masked skin refiner on Qwen-Image-2.1 (PROD :8188).

Why 2.1: the masked refiner with Qwen-Image-Edit 2509/2511 (same architecture as the
render) turns the skin flat grey - the model re-renders masked skin as a matte surface
instead of skin (measured: warm pixels in the thigh region drop from 82% to 12-15%).
Qwen-Image-2.1 is a DIFFERENT generation, so its skin prior should not collapse the same
way.

Graph (native 2.1, official conditioning):
  LoadImage(src) + LoadImage(mask) -> ImageToMask -> VAEEncodeForInpaint
  UNETLoader(2.1) + CLIPLoader(qwen3vl_8b, type qwen_image) + VAELoader(2.1 vae)
  + LoraLoaderModelOnly -> TextEncodeQwenImage21(images={image_1: src}) -> KSampler
  -> VAEDecode -> SaveImage
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request

SCRATCH = r"C:\Users\user\AppData\Local\hermes\cache\scratch"
PROD_DIR = r"E:\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable"
PROD_IN = os.path.join(PROD_DIR, "ComfyUI", "input")
PROD_OUT = os.path.join(PROD_DIR, "ComfyUI", "output")
BASE = "http://127.0.0.1:8188"

UNET = os.environ.get("Q21_UNET", "qwen_image_2.1_int8_convrot.safetensors")
CLIP = os.environ.get("Q21_CLIP", "qwen3vl_8b_int8_convrot.safetensors")
VAE = os.environ.get("Q21_VAE", "qwen_image_2.1_vae_bf16.safetensors")
LORA = os.environ.get("Q21_LORA", "")
LORA_STR = float(os.environ.get("Q21_LORA_STRENGTH", "1.0"))

POS = ("Natural human skin with visible pores, fine epidermal texture and subtle tonal "
       "variation, photographic skin surface, soft subsurface scattering. The same woman, "
       "the same body, the same pose, the same hands, the same hair and the same background. "
       "Warm natural skin tone matching the rest of her body. Real skin, not plastic, "
       "not airbrushed, not waxy, not smooth, no body hair, no fuzz, no extra fingers, "
       "no extra limbs, no distortions")
NEG = ("plastic skin, airbrushed skin, waxy skin, matte grey skin, smooth skin, mannequin, "
       "doll, body hair, fuzz, grey flat skin, blurry, deformed, extra limbs, extra fingers, "
       "text, watermark")


def api(path, payload=None, timeout=180):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def run(tag: str, *, src: str, mask: str, denoise: float = 0.35, steps: int = 24,
        cfg: float = 4.0, seed: int = 1234, grow: int = 8, size=(1088, 1624)):
    shutil.copy2(os.path.join(SCRATCH, src), os.path.join(PROD_IN, f"q21r_{tag}_src.png"))
    shutil.copy2(os.path.join(SCRATCH, mask), os.path.join(PROD_IN, f"q21r_{tag}_mask.png"))
    nodes = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "5": {"class_type": "LoadImage", "inputs": {"image": f"q21r_{tag}_src.png"}},
        "6": {"class_type": "LoadImage", "inputs": {"image": f"q21r_{tag}_mask.png"}},
        "7": {"class_type": "ImageToMask", "inputs": {"image": ["6", 0], "channel": "red"}},
        "8": {"class_type": "VAEEncodeForInpaint",
              "inputs": {"pixels": ["5", 0], "vae": ["3", 0], "mask": ["7", 0], "grow_mask_by": grow}},
        "9": {"class_type": "TextEncodeQwenImage21",
              "inputs": {"clip": ["2", 0], "prompt": POS, "negative_prompt": NEG,
                         "vae": ["3", 0], "resolution": 0, "images": {"image_1": ["5", 0]}}},
        "10": {"class_type": "KSampler",
               "inputs": {"model": ["1", 0], "seed": seed, "steps": steps, "cfg": cfg,
                          "sampler_name": "euler", "scheduler": "simple", "denoise": denoise,
                          "positive": ["9", 0], "negative": ["9", 1], "latent_image": ["8", 0]}},
        "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["3", 0]}},
        "12": {"class_type": "SaveImage", "inputs": {"images": ["11", 0], "filename_prefix": "q21r_" + tag}},
    }
    if LORA:
        nodes["20"] = {"class_type": "LoraLoaderModelOnly",
                       "inputs": {"model": ["1", 0], "lora_name": LORA,
                                  "strength_model": LORA_STR, "strength_clip": 0.0}}
        nodes["10"]["inputs"]["model"] = ["20", 0]
    try:
        resp = api("/prompt", {"prompt": nodes, "client_id": "q21r"}, timeout=240)
    except urllib.error.HTTPError as e:
        print(f"{tag}: HTTP {e.code} {e.read().decode('utf-8','replace')[:700]}", flush=True)
        return None
    pid = resp.get("prompt_id")
    if not pid:
        print(f"{tag}: нет prompt_id {json.dumps(resp)[:400]}", flush=True)
        return None
    print(f"{tag}: prompt_id={pid} denoise={denoise} cfg={cfg} steps={steps}", flush=True)
    t0 = time.time()
    while time.time() - t0 < 2400:
        time.sleep(12)
        try:
            hist = api(f"/history/{pid}", timeout=60)
        except Exception:
            continue
        e = hist.get(pid)
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
                    print(f"{tag}: ГОТОВО за {int(time.time()-t0)} с -> {dst}", flush=True)
                    return dst
    print(f"{tag}: ТАЙМАУТ", flush=True)
    return None


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "FINAL_skin_stand2511.png"
    mask = sys.argv[2] if len(sys.argv) > 2 else "mask_refine_skin.png"
    for dn in (0.30, 0.45):
        run(f"Q21R_{int(dn*100)}", src=src, mask=mask, denoise=dn, steps=24, cfg=4.0, seed=1234)
