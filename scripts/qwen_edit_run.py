# -*- coding: utf-8 -*-
"""Qwen-Image-Edit runner (PROD :8188).

VAU NOTE: the stock ComfyUI Qwen autoencoder (autoencoder_kl_qwenimage.py) is a
3D/video VAE - loading it raises "size mismatch ... encoder.conv1.weight
[96,4,1,3,3] vs [160,12,3,3,3]". Use the 2D VAE qwen_image_vae_2d.safetensors.

Original docstring follows.

python qwen_edit.py <tag> <input_name_in_prod_input> <seed> <denoise> <prompt_file> [steps]
Результат: scratch/qe_<tag>.png
"""
import json
import os
import sys
import time
import urllib.request


BASE = os.environ.get("COMFYUI_PROD_URL", "http://127.0.0.1:8188")
# The stock ComfyUI Qwen autoencoder is a 3D/video VAE: loading it raises
# "size mismatch ... encoder.conv1.weight [96,4,1,3,3] vs [160,12,3,3,3]".
VAE_NAME = "qwen_image_vae_2d.safetensors"
PROD_OUT = os.environ.get("COMFYUI_PROD_OUTPUT", r"E:\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable\ComfyUI\output")
SCRATCH = os.environ.get("HERMES_SCRATCH_DIR", r"C:\Users\user\AppData\Local\hermes\cache\scratch")
NEG = ("blurry, deformed, extra limbs, extra fingers, fused bodies, cartoon, illustration, "
       "painting, text, watermark, floating body, duplicated body, disconnected limbs, "
       "gigantic male body, oversized torso, two men, three heads, mannequin, doll, "
       "man sitting, man crouching, man kneeling, man standing far away, gap between the bodies")


def run(tag, img_name, positive, seed, denoise=1.0, steps=30, size=(736, 992)):
    graph = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": os.environ.get("QWEN_EDIT_MODEL", "qwen_image_edit_2509_fp8_e4m3fn.safetensors"), "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_NAME}},
        "4": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["1", 0], "lora_name": "qwen_image_edit_plus_nsfw_lora_safetensors.safetensors",
                         "strength_model": 1.0, "strength_clip": 0.0}},
        "5": {"class_type": "LoadImage", "inputs": {"image": img_name}},
        "6": {"class_type": "QwenImageIntegratedKSampler",
              "inputs": {"model": ["4", 0], "clip": ["2", 0], "vae": ["3", 0], "image1": ["5", 0],
                         "positive_prompt": positive, "negative_prompt": NEG,
                         "generation_mode": "图生图 image-to-image", "batch_size": 1,
                         "width": size[0], "height": size[1], "seed": seed, "steps": steps, "cfg": 1.0,
                         "sampler_name": "euler", "scheduler": "simple", "denoise": denoise,
                         "output_filename_prefix": "qedit_" + tag}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "qedit_" + tag}},
    }
    req = urllib.request.Request(BASE + "/prompt", data=json.dumps({"prompt": graph}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    pid = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())["prompt_id"]
    print("[%s] принято %s seed=%d denoise=%.2f" % (tag, pid[:8], seed, denoise), flush=True)
    for _ in range(300):
        h = json.loads(urllib.request.urlopen("%s/history/%s" % (BASE, pid), timeout=30).read().decode())
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                print("[%s] ERROR %s" % (tag, str(st.get("messages"))[:250]), flush=True)
                return 1
            for _, o in h[pid].get("outputs", {}).items():
                for im in o.get("images", []):
                    p = os.path.join(PROD_OUT, im.get("subfolder", ""), im["filename"])
                    if not p.lower().endswith(".png"):
                        p += ".png"
                    from PIL import Image  # lazy: not every environment has Pillow

                    out = os.path.join(SCRATCH, "qe_%s.png" % tag)
                    Image.open(p).convert("RGB").resize(size, Image.LANCZOS).save(out)
                    print("[%s] сохранено %s" % (tag, out), flush=True)
            return 0
        time.sleep(5)
    print("[%s] таймаут" % tag, flush=True)
    return 2


if __name__ == "__main__":
    tag, img_name = sys.argv[1], sys.argv[2]
    seed = int(sys.argv[3])
    denoise = float(sys.argv[4])
    prompt = open(sys.argv[5], encoding="utf-8").read()
    steps = int(sys.argv[6]) if len(sys.argv) > 6 else 30
    size = (int(sys.argv[7]), int(sys.argv[8])) if len(sys.argv) > 8 else (736, 992)
    sys.exit(run(tag, img_name, prompt, seed, denoise, steps, size))
