"""Z-Image-Turbo detail pass: the first real shot at photographic skin.

Official ComfyUI graphs (Comfy-Org/workflow_templates):
  templates/image_z_image_turbo.json      -> CLIPLoader(qwen_3_4b, type='lumina2'),
                                            UNETLoader(z_image_turbo), VAELoader(ae.safetensors),
                                            ModelSamplingAuraFlow(shift=3),
                                            KSampler(steps=8, cfg=1, res_multistep, simple)
  blueprints/image_upscale_z_image_turbo.json -> the DETAIL pass: upscale -> VAEEncode ->
                                            KSampler(steps=5, cfg=1, dpmpp_2m_sde, beta, denoise=0.33)

Why Z-Image: Qwen-Image (2509 and 2.1) never draws skin pores - verified across cfg, denoise,
four resolutions and three skin LoRAs, including native 2K. Z-Image is a different
architecture (S3-DiT, 6B, Alibaba) with a much stronger photographic prior, and there is a
dedicated skin-texture LoRA for it (rh-skin-texture-v2.1-lora, trigger words "detailed,
incredibly lifelike, cinematic, realistic, sharp").

The pass starts from the VAE latent of our frame at denoise 0.33, so it re-renders surface
detail while keeping pose, face, body and background.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request

import local_paths

SCRATCH = str(local_paths.SCRATCH)
PROD_DIR = str(local_paths.PROD_DIR)
PROD_IN = str(local_paths.PROD_IN)
PROD_OUT = str(local_paths.PROD_OUT)
BASE = "http://127.0.0.1:8188"

UNET = os.environ.get("ZI_UNET", "z_image_turbo_fp8.safetensors")
VAE = os.environ.get("ZI_VAE", "z_image_ae.safetensors")
CLIP = os.environ.get("ZI_CLIP", "qwen_3_4b_fp8_mixed.safetensors")
LORA = "z_image_skin_texture_v2.1.safetensors"
UPSCALE = "4x-UltraSharp.pth"


def api(path, payload=None, timeout=180):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def run(tag: str, *, src: str, denoise: float = 0.33, steps: int = 5,
        lora_strength: float = 0.8, upscale: float = 2.0, seed: int = 4242,
        w: int = 1088, h: int = 1620):
    pos = ("detailed, incredibly lifelike, cinematic, realistic, sharp. "
           "Photorealistic studio photograph of a nude adult woman, natural human skin with "
           "visible pores and fine epidermal texture, soft subsurface scattering, natural skin "
           "tone variation. Keep her exact face, hair, pose, hands, body proportions and the "
           "grey studio backdrop. natural realistic skin texture with pores, no plastic skin, "
           "no airbrushing, no waxy skin, no body hair, no fuzz, anatomically correct proportions")
    neg = ("plastic skin, airbrushed skin, waxy skin, smooth skin, mannequin, doll, "
           "body hair, fuzz, stubble, grainy skin, noisy skin, blurry, deformed, extra limbs, "
           "extra fingers, text, watermark")
    nodes = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP, "type": "lumina2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "5": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["4", 0], "lora_name": LORA,
                         "strength_model": lora_strength, "strength_clip": 0.0}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": pos}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": neg}},
        "8": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["7", 0]}},
        "9": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": UPSCALE}},
        "10": {"class_type": "ImageUpscaleWithModel",
               "inputs": {"upscale_model": ["9", 0], "image": ["11a", 0]}},
        "11": {"class_type": "LoadImage", "inputs": {"image": os.environ.get("ZI_SRC_NAME", "zi_in.png")}},
        "11a": {"class_type": "ImageScaleBy",
                "inputs": {"upscale_method": "lanczos", "image": ["11", 0],
                           "scale_by": float(os.environ.get("ZI_PRE", "0.5"))}},
        "11b": {"class_type": "ImageScaleBy",
                "inputs": {"upscale_method": "lanczos", "image": ["10", 0],
                           "scale_by": 0.5}},
        "12": {"class_type": "VAEEncode", "inputs": {"pixels": ["11b", 0], "vae": ["3", 0]}},
        "13": {"class_type": "KSampler",
               "inputs": {"model": ["5", 0], "seed": seed, "steps": steps, "cfg": 1.0,
                          "sampler_name": "dpmpp_2m_sde", "scheduler": "beta",
                          "denoise": denoise,
                          "positive": ["6", 0], "negative": ["8", 0], "latent_image": ["12", 0]}},
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["3", 0]}},
        "15": {"class_type": "ImageScaleBy", "inputs": {"upscale_method": "lanczos",
                                                        "image": ["14", 0],
                                                        "scale_by": 1.0}},
        "16": {"class_type": "SaveImage", "inputs": {"images": ["15", 0], "filename_prefix": tag}},
    }
    shutil.copy2(os.path.join(SCRATCH, src), os.path.join(PROD_IN, "zi_in.png"))

    try:
        resp = api("/prompt", {"prompt": nodes, "client_id": "zi"}, timeout=240)
    except urllib.error.HTTPError as e:
        print(f"{tag}: HTTP {e.code} {e.read().decode('utf-8','replace')[:700]}", flush=True)
        return None
    pid = resp.get("prompt_id")
    if not pid:
        print(f"{tag}: нет prompt_id {json.dumps(resp)[:400]}", flush=True)
        return None
    print(f"{tag}: prompt_id={pid} denoise={denoise} steps={steps} lora={lora_strength}", flush=True)
    t0 = time.time()
    while time.time() - t0 < 2400:
        time.sleep(12)
        try:
            hist = api(f"/history/{pid}", timeout=45)
        except Exception:
            continue
        e = hist.get(pid)
        if not e:
            continue
        if e.get("status", {}).get("status_str") == "error":
            print(f"{tag}: ОШИБКА {json.dumps(e.get('status',{}).get('messages',[]))[:500]}", flush=True)
            return None
        for o in e.get("outputs", {}).values():
            for im in o.get("images", []):
                p = os.path.join(PROD_OUT, im.get("subfolder", ""), im["filename"])
                if os.path.exists(p):
                    dst = os.path.join(SCRATCH, f"{tag}.png")
                    shutil.copy2(p, dst)
                    print(f"{tag}: ГОТОВО за {int(time.time()-t0)} с -> {dst} "
                          f"({os.path.getsize(dst)//1024} КБ)", flush=True)
                    return dst
    print(f"{tag}: ТАЙМАУТ", flush=True)
    return None


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "qe_graded_w0.9.png"
    tag = sys.argv[2] if len(sys.argv) > 2 else "zi_detail"
    run(tag, src=src, denoise=0.33, steps=5, lora_strength=0.8)
