# -*- coding: utf-8 -*-
"""Short native H3 Ref2VA A/B for prompt style embeddings (first frame)."""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
from pathlib import Path

import requests

from probe_skin_render import acquire_render_lock, gpu_sample, release_render_lock

BASE = "http://127.0.0.1:8188"
PROMPT = """{style}r34l1sm
subject_definitions:
<Subject 1> is the adult woman in <Picture 1>, with the same face, wet dark hair and natural skin.
summary:
[reference generation] A static-camera documentary shot of <Subject 1> in the same white bathtub and rundown bathroom as <Picture 1>. She breathes, blinks and moves her fingers naturally.
retention_analysis:
<Subject 1>: fully_preserved - facial identity, body proportions, pose, wet hair, natural skin colour and the existing fine skin texture of <Picture 1>.
detailed_description:
[Shot 1] Photorealistic live-action footage, natural damp skin with fine pores and subtle pigment variation, soft directional light, no beauty retouching. Static camera, no cuts.
overall_soundscape: Quiet bathroom room tone and gentle water sounds.
non_diegetic_music: N/A."""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--style", default="", help="embedding name without embedding: prefix")
    ap.add_argument("--seed", type=int, default=73571)
    ap.add_argument("--frames", type=int, default=39)
    ap.add_argument("--steps", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    acquire_render_lock(f"probe_h3_ref_style {args.tag}")
    try:
        with args.source.open("rb") as handle:
            response = requests.post(BASE + "/upload/image", files={"image": (args.source.name, handle, "image/png")}, data={"type": "input"}, timeout=60)
        response.raise_for_status()
        uploaded = response.json()["name"]
        style = f"embedding:{args.style}\n" if args.style else ""
        prompt = PROMPT.format(style=style)
        graph = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_ref2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "type": "minimax", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_int8_convrot.safetensors"}},
            "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors", "strength_model": 1.0}},
            "5": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["4", 0], "lora_name": "Realism People - MinimaxH3 - r34l1sm,0.6-1str.safetensors", "strength_model": 0.65}},
            "6": {"class_type": "LoadImage", "inputs": {"image": uploaded}},
            "7": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
                "clip": ["2", 0], "vae": ["3", 0], "ref_images.ref_image_0": ["6", 0],
                "prompt": prompt, "width": 736, "height": 1120, "length": args.frames, "ref_image_size": "match",
            }},
            "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": args.seed}},
            "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
            "10": {"class_type": "BasicScheduler", "inputs": {"model": ["5", 0], "scheduler": "beta", "steps": args.steps, "denoise": 1.0}},
            "11": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["7", 0]}},
            "12": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["8", 0], "guider": ["11", 0], "sampler": ["9", 0], "sigmas": ["10", 0], "latent_image": ["7", 1]}},
            "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
            "14": {"class_type": "ImageFromBatch", "inputs": {"image": ["13", 0], "batch_index": 0, "length": 1}},
            "15": {"class_type": "SaveImage", "inputs": {"images": ["14", 0], "filename_prefix": f"probe_{args.tag}"}},
        }
        (args.out_dir / f"{args.tag}.workflow.json").write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
        started = time.monotonic()
        response = requests.post(BASE + "/prompt", json={"prompt": graph, "client_id": "probe-h3-ref-style"}, timeout=60)
        if response.status_code >= 400:
            raise RuntimeError(response.text)
        prompt_id = response.json()["prompt_id"]
        deadline = started + args.timeout
        while time.monotonic() < deadline:
            history = requests.get(BASE + f"/history/{prompt_id}", timeout=30).json()
            entry = history.get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError(json.dumps(status.get("messages", []), ensure_ascii=False))
                for output in entry.get("outputs", {}).values():
                    for item in output.get("images", []):
                        query = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""), "type": item.get("type", "output")})
                        content = requests.get(BASE + "/view?" + query, timeout=120).content
                        out = args.out_dir / f"{args.tag}_f000.png"
                        out.write_bytes(content)
                        summary = {"tag": args.tag, "style": args.style, "seed": args.seed, "frames": args.frames, "steps": args.steps, "elapsed_seconds": round(time.monotonic() - started, 2), "file": str(out), "gpu": gpu_sample()}
                        (args.out_dir / f"{args.tag}.summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
                        print(json.dumps(summary, indent=2), flush=True)
                        return 0
            time.sleep(3)
        requests.post(BASE + "/interrupt", timeout=10)
        raise TimeoutError(f"native H3 probe exceeded {args.timeout}s")
    finally:
        release_render_lock()


if __name__ == "__main__":
    raise SystemExit(main())
