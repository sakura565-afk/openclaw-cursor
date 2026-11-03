# -*- coding: utf-8 -*-
"""FLUX.1-dev img2img: build a start frame with real skin micro-texture.

WHY THIS EXISTS
---------------
Our measured ceiling on video skin was 6.5/10. The cause was not the video
model - it was the START FRAME. Every still we had was produced by Qwen-Image
-Edit 2511 / Z-Image, and both flatten skin: the pores are simply not in the
image before the video model ever sees it. Post passes cannot invent a texture
that the generation stage never produced - we measured that ITF-SkinDiffDetail
scores 8/10 on a still and adds nothing in motion, because a still-only texture
has no temporal support.

FLUX.1-dev renders natively at full resolution and its skin is generated at the
correct physical scale, so an img2img pass at moderate denoise can put pores
into the start frame itself. That is the lever this script pulls.

USAGE
-----
  python scripts/flux_start_frame.py --image in.png --out out.png \
      --denoise 0.42 --steps 24 --seed 7

  # low-denoise "detail only" pass (keep the pose, add micro-texture)
  python scripts/flux_start_frame.py --image in.png --out out.png --denoise 0.30

Requires on disk:
  diffusion_models/flux1-dev-fp8.safetensors
  text_encoders/clip_l.safetensors + text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors
  vae/ae.safetensors
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

PROD = os.environ.get("COMFYUI_PROD", "http://127.0.0.1:8188")


def _post_json(url: str, payload: dict, timeout: int = 120) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json(url: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "h3"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def upload_image(path: str, server: str = PROD) -> str:
    """Upload a local image into ComfyUI's input dir, return its stored name."""
    import cgi
    boundary = uuid.uuid4().hex.encode()
    name = os.path.basename(path)
    with open(path, "rb") as fh:
        payload = fh.read()
    body = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="image"; filename="' + name.encode() + b'"\r\n'
        b"Content-Type: image/png\r\n\r\n" + payload + b"\r\n"
        b"--" + boundary + b"--\r\n"
    )
    req = urllib.request.Request(
        server + "/upload/image", data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary.decode()})
    with urllib.request.urlopen(req, timeout=180) as r:
        info = json.loads(r.read().decode("utf-8"))
    return info.get("name") or info.get("subfolder", "") + "/" + name


def build_graph(image_name: str, denoise: float, steps: int, seed: int,
                cfg: float, guidance: float, width: int, height: int,
                flux_name: str, clip_l: str, t5: str, vae_name: str,
                lora: str = "", lora_strength: float = 0.0) -> dict:
    """FLUX.1-dev img2img graph.

    The image is VAE-encoded and the latent is noised to `denoise`; FLUX then
    regenerates it. `FluxGuidance` sets the real guidance (FLUX does not use
    the classic CFG slider the same way), so cfg stays at 1.0.
    """
    g: dict = {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}},
        "3": {"class_type": "UNETLoader",
              "inputs": {"unet_name": flux_name, "weight_dtype": "fp8_e4m3fn"}},
        "4": {"class_type": "DualCLIPLoader",
              "inputs": {"clip_name1": clip_l, "clip_name2": t5, "type": "flux"}},
        "5": {"class_type": "VAEEncode",
              "inputs": {"pixels": ["1", 0], "vae": ["2", 0]}},
        "6": {"class_type": "ModelSamplingFlux",
              "inputs": {"model": ["3", 0], "max_shift": 1.15, "base_shift": 0.5,
                         "width": width, "height": height}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"text": "", "clip": ["4", 0]}},
        "8": {"class_type": "FluxGuidance",
              "inputs": {"conditioning": ["7", 0], "guidance": guidance}},
        "9": {"class_type": "KSampler",
              "inputs": {"model": ["6", 0], "positive": ["8", 0], "negative": ["8", 0],
                         "latent_image": ["5", 0], "seed": seed, "steps": steps,
                         "cfg": cfg, "sampler_name": "euler", "scheduler": "simple",
                         "denoise": denoise}},
        "10": {"class_type": "VAEDecode",
               "inputs": {"samples": ["9", 0], "vae": ["2", 0]}},
        "11": {"class_type": "SaveImage",
               "inputs": {"filename_prefix": "flux_start", "images": ["10", 0]}},
    }
    if lora and lora_strength > 0:
        g["12"] = {"class_type": "LoraLoaderModelOnly",
                   "inputs": {"lora_name": lora, "strength_model": lora_strength,
                              "model": ["6", 0]}}
        g["9"]["inputs"]["model"] = ["12", 0]
    return g


def wait_for(server: str, prompt_id: str, timeout: int = 3600) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            h = _get_json(server + "/history/" + prompt_id, timeout=60)
        except urllib.error.URLError:
            time.sleep(4)
            continue
        if prompt_id in h:
            entry = h[prompt_id]
            if "outputs" in entry:
                return entry
            if entry.get("status", {}).get("status_str") == "error":
                raise RuntimeError(json.dumps(entry.get("status"), ensure_ascii=False)[:900])
        time.sleep(4)
    raise TimeoutError(f"prompt {prompt_id} не завершился за {timeout}s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--denoise", type=float, default=0.42)
    ap.add_argument("--steps", type=int, default=24)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--guidance", type=float, default=3.5)
    ap.add_argument("--width", type=int, default=0)
    ap.add_argument("--height", type=int, default=0)
    ap.add_argument("--flux", default="flux1-dev-fp8.safetensors")
    ap.add_argument("--clip-l", default="clip_l.safetensors")
    ap.add_argument("--t5", default="t5xxl_fp8_e4m3fn_scaled.safetensors")
    ap.add_argument("--vae", default="ae.safetensors")
    ap.add_argument("--lora", default="")
    ap.add_argument("--lora-strength", type=float, default=0.0)
    ap.add_argument("--server", default=PROD)
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args()

    from PIL import Image
    im = Image.open(args.image).convert("RGB")
    width = args.width or im.width
    height = args.height or im.height

    # FLUX works on multiples of 16; keep the aspect ratio the caller asked for.
    width = max(64, (width // 16) * 16)
    height = max(64, (height // 16) * 16)

    # preflight: the models must actually be visible to this ComfyUI instance
    try:
        oi = _get_json(args.server + "/object_info", timeout=60)
    except Exception as e:
        print(f"ComfyUI недоступен на {args.server}: {e}", file=sys.stderr)
        return 2
    def has(node: str, key: str, value: str) -> bool:
        try:
            opts = oi[node]["input"]["required"][key][0]
        except Exception:
            return False
        return value in opts
    missing = []
    if not has("UNETLoader", "unet_name", args.flux):
        missing.append(f"unet_name={args.flux}")
    if not has("DualCLIPLoader", "clip_name2", args.t5):
        missing.append(f"t5={args.t5}")
    if not has("VAELoader", "vae_name", args.vae):
        missing.append(f"vae={args.vae}")
    if missing:
        print(f"ComfyUI не видит: {', '.join(missing)}", file=sys.stderr)
        return 2

    name = upload_image(args.image, args.server)
    graph = build_graph(name, args.denoise, args.steps, args.seed, args.cfg,
                        args.guidance, width, height, args.flux, args.clip_l,
                        args.t5, args.vae, args.lora, args.lora_strength)
    client_id = uuid.uuid4().hex
    res = _post_json(args.server + "/prompt",
                     {"prompt": graph, "client_id": client_id}, timeout=180)
    pid = res.get("prompt_id")
    if not pid:
        print("ComfyUI отклонил граф: " + json.dumps(res, ensure_ascii=False)[:900], file=sys.stderr)
        return 3
    print(f"prompt_id={pid} denoise={args.denoise} steps={args.steps} {width}x{height}", flush=True)
    entry = wait_for(args.server, pid, args.timeout)
    outs = entry.get("outputs", {}).get("11", {}).get("images", [])
    if not outs:
        print("нет выходного изображения", file=sys.stderr)
        return 4
    src = outs[0]
    url = f"{args.server}/view?filename={src['filename']}&subfolder={src.get('subfolder','')}&type=output"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "h3"}),
                                timeout=300) as r:
        data = r.read()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "wb") as fh:
        fh.write(data)
    print(json.dumps({"out": args.out, "bytes": len(data), "prompt_id": pid,
                      "denoise": args.denoise, "steps": args.steps,
                      "size": [width, height]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
