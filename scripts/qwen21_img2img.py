"""2.1 as a TRUE img2img: start from the VAE latent of our frame, denoise < 1.

With the latent coming from the text encoder (an empty latent sized to image_1) and
denoise 1.0, 2.1 regenerates the scene - the pose was completely changed (the model
seated the woman cross-legged). To preserve pose/face/background, the sampler must start
from the VAE latent of the reference frame and only partially denoise it.

Reference 2 = the real skin photograph, so the model has authentic pore-scale texture to
copy instead of inventing it. The prompt addresses the references as <image1>/<image2>.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request

def _resolve_prod_dir() -> str:
    """Корень ComfyUI portable: env-переопределение, иначе поиск на дисках.

    Путь не зашит в исходник — иначе guard-тест CI
    (tests/test_no_absolute_paths.py) ломает сборку на других хостах.
    """
    env = os.environ.get("COMFYUI_PROD_DIR")
    if env:
        return env
    import glob

    for letter in "CDEFGH":
        for hit in glob.glob(f"{letter}:/ComfyUI_windows_portable*/ComfyUI_windows_portable"):
            return hit
    raise SystemExit(
        "COMFYUI_PROD_DIR не задан и ComfyUI portable не найден на дисках C-H"
    )


SCRATCH = os.environ.get(
    "HERMES_SCRATCH_DIR",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "cache", "scratch"),
)
PROD_DIR = _resolve_prod_dir()
PROD_IN = os.path.join(PROD_DIR, "ComfyUI", "input")
PROD_OUT = os.path.join(PROD_DIR, "ComfyUI", "output")
BASE = "http://127.0.0.1:8188"

UNET = "qwen_image_2.1_int8_convrot.safetensors"
VAE = "qwen_image_2.1_vae_bf16.safetensors"
CLIP = "qwen3vl_8b_int8_convrot.safetensors"
LORA = "NSFW Qwen 2.1 Lora.safetensors"


def api(path, payload=None, timeout=180):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def run(tag: str, *, denoise: float, steps: int = 25, cfg: float = 1.0,
        skin_ref: bool = True, seed: int = 88231, w: int = 1088, h: int = 1620,
        src: str = "qe_undress_src_hi.png"):
    pos = ("<image1> is a studio photograph of an adult woman. Remove her green ribbed "
           "bodysuit completely: she is now fully nude, wearing nothing. Her skin is real "
           "human skin with visible pores and fine epidermal texture, natural warm skin "
           "tone, soft subsurface scattering, matching the skin detail of <image2>. "
           "Keep her face, hair, expression, pose, raised hand at the collarbone, the arm "
           "behind her back, her natural body proportions, the grey studio backdrop and the "
           "framing exactly as in <image1>. "
           "no plastic skin, no airbrushing, no smooth skin, no mannequin, no waxy skin, "
           "no body hair, no fuzz, no pinched waist, no wasp waist, anatomically correct hands")
    neg = ("green fabric, bodysuit, clothing, garment, cloth, smooth skin, plastic skin, "
           "airbrushed skin, waxy skin, mannequin, doll, body hair, fuzz on the skin, "
           "pinched waist, wasp waist, elongated torso, blotchy pigmentation, grainy skin, "
           "blurry, deformed, extra limbs, extra fingers, text, watermark")
    nodes = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["1", 0], "lora_name": LORA,
                         "strength_model": 1.0, "strength_clip": 0.0}},
        "5": {"class_type": "LoadImage", "inputs": {"image": "c21_ref.png"}},
        "7": {"class_type": "TextEncodeQwenImage21",
              "inputs": {"clip": ["2", 0], "prompt": pos, "negative_prompt": neg,
                         "vae": ["3", 0], "resolution": 0, "images": {"image_1": ["5", 0]}}},
        "8": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["3", 0]}},
        "12": {"class_type": "KSampler",
               "inputs": {"model": ["4", 0], "seed": seed, "steps": steps, "cfg": cfg,
                          "sampler_name": "euler", "scheduler": "simple", "denoise": denoise,
                          "positive": ["7", 0], "negative": ["7", 1], "latent_image": ["8", 0]}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": tag}},
    }
    if skin_ref:
        shutil.copy2(os.path.join(SCRATCH, "skin_real_menschliche_haut.jpg"),
                     os.path.join(PROD_IN, "c21_skinref.png"))
        nodes["6"] = {"class_type": "LoadImage", "inputs": {"image": "c21_skinref.png"}}
        nodes["7"]["inputs"]["images"] = {"image_1": ["5", 0], "image_2": ["6", 0]}

    shutil.copy2(os.path.join(SCRATCH, src), os.path.join(PROD_IN, "c21_ref.png"))

    try:
        resp = api("/prompt", {"prompt": nodes, "client_id": "c21v"}, timeout=240)
    except urllib.error.HTTPError as e:
        print(f"{tag}: HTTP {e.code} {e.read().decode('utf-8','replace')[:600]}", flush=True)
        return None
    pid = resp.get("prompt_id")
    if not pid:
        print(f"{tag}: нет prompt_id {json.dumps(resp)[:300]}", flush=True)
        return None
    print(f"{tag}: prompt_id={pid} denoise={denoise} steps={steps} skinref={skin_ref}", flush=True)
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
            print(f"{tag}: ОШИБКА {json.dumps(e.get('status',{}).get('messages',[]))[:400]}", flush=True)
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
    if len(sys.argv) > 1 and sys.argv[1] == "matrix":
        for tag, dn, sr in (("v21_d85", 0.85, True), ("v21_d65", 0.65, True),
                            ("v21_d85_1ref", 0.85, False)):
            run(tag, denoise=dn, skin_ref=sr)
    else:
        run("v21_probe", denoise=0.85, skin_ref=True)
