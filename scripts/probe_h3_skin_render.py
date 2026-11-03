# -*- coding: utf-8 -*-
"""H3 skin-texture probe: renders one clip and measures micro-texture.

Renders through PROD ComfyUI (:8188) with MiniMaxH3 and writes an mp4 that
scripts/probe_h3_vs_fv.py can measure. Supports every config axis we need to
compare:

  * mode        fl2va (first frame pinned) | ref2va (reference image + prompt)
  * turbo       the 4-step speed LoRA (ref2v) or none (full 20 steps)
  * style       a minimaxh3_* embedding (style_models / embeddings folder)
  * lora        the Realism People r34l1sm LoRA with a strength

Why a dedicated runner: scripts/h3_i2v_run.py mixes in an audio mux step and a
7.3 s ffmpeg remux that this measurement does not need, and it hard-codes the
LoRA wiring; this probe keeps the graph explicit so a broken config is visible
in the saved workflow JSON.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

BASE = "http://127.0.0.1:8188"
try:
    from local_paths import PROD_IN, PROD_OUT
except ImportError:  # Direct execution puts scripts/ on sys.path.
    from local_paths import PROD_IN, PROD_OUT

FL2VA_UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
REF2VA_UNET = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
TE_INT8 = "qwen3vl_32b_minimax_h3_int8_convrot.safetensors"
VAE_FP16 = "minimax_h3_video_vae_fp16.safetensors"
VAE_INT8 = "minimax_h3_video_vae_int8_convrot.safetensors"
TURBO_REF2V = "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"
TURBO_FL2VA = "minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors"
REALISM_LORA = "Realism People - MinimaxH3 - r34l1sm,0.6-1str.safetensors"

I2V_PROMPT = """A photorealistic documentary shot in the same bathroom as the first frame: the same white bathtub, the same beige stone wall, the same soft warm light, the same adult woman with wet dark wavy hair and red lipstick, the same bare skin. She reclines in the tub with her legs bent and overlapping, one knee raised in the foreground. The camera holds the same slightly elevated close framing at the same distance and never moves. She breathes, blinks, shifts her weight slightly, her hair slides across her shoulder, the water and the foam ripple gently. Deep focus and sharp at every moment: individual water droplets on her wet skin, the strands of her wet hair, the foam bubbles. Real human skin with natural pores and natural colour variations, natural female anatomy, no distortions, no extra fingers, no extra limbs. Static camera, continuous shot, no cuts."""

REF2V_PROMPT = """{style}subject_definitions:
<Subject 1> is the adult woman in <Picture 1>, with the same face, wet dark hair and natural skin.
summary:
[reference generation] A static-camera documentary shot of <Subject 1> in the same white bathtub and rundown bathroom as <Picture 1>. She reclines in the tub with her legs bent and overlapping, one knee raised in the foreground. She breathes, blinks and moves her fingers naturally.
retention_analysis:
<Subject 1>: fully_preserved - facial identity, body proportions, pose, wet hair, natural skin colour and the existing fine skin texture of <Picture 1>.
detailed_description:
[Shot 1] Photorealistic live-action footage, natural damp skin with fine pores and subtle pigment variation, soft directional light, no beauty retouching. Static camera, no cuts.
overall_soundscape: Quiet bathroom room tone and gentle water sounds.
non_diegetic_music: N/A."""


def upload(image: Path) -> str:
    with image.open("rb") as handle:
        response = requests.post(
            BASE + "/upload/image",
            files={"image": (image.name, handle, "image/png")},
            data={"type": "input", "overwrite": "true"},
            timeout=120,
        )
    response.raise_for_status()
    return response.json()["name"]


def build_graph(args, uploaded: str) -> dict:
    nodes: dict[str, dict] = {}
    nodes["1"] = {"class_type": "UNETLoader", "inputs": {
        "unet_name": REF2VA_UNET if args.mode == "ref2va" else FL2VA_UNET,
        "weight_dtype": "default"}}
    nodes["2"] = {"class_type": "CLIPLoader", "inputs": {
        "clip_name": TE_INT8, "type": "minimax"}}
    nodes["3"] = {"class_type": "VAELoader", "inputs": {
        "vae_name": VAE_INT8 if args.mode == "ref2va" else VAE_FP16}}

    model_ref = ["1", 0]
    nid = 10
    if args.turbo:
        turbo_name = TURBO_REF2V if args.mode == "ref2va" else TURBO_FL2VA
        nodes[str(nid)] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": model_ref, "lora_name": turbo_name, "strength_model": 1.0}}
        model_ref = [str(nid), 0]
        nid += 1
    if args.lora:
        nodes[str(nid)] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": model_ref, "lora_name": REALISM_LORA, "strength_model": args.lora_strength}}
        model_ref = [str(nid), 0]
        nid += 1

    nodes["20"] = {"class_type": "LoadImage", "inputs": {"image": uploaded}}
    if args.mode == "ref2va":
        style = f"embedding:{args.style}\n" if args.style else ""
        nodes["21"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0],
            "ref_images": [["20", 0]],
            "prompt": REF2V_PROMPT.format(style=style),
            "width": args.width, "height": args.height, "length": args.length,
            "ref_image_size": "match"}}
        positive = ["21", 0]
        latent = ["21", 1]
        sampler_name = "res_multistep"
        scheduler = "beta"
    else:
        nodes["21"] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0],
            "first_frame": ["20", 0],
            "prompt": I2V_PROMPT,
            "width": args.width, "height": args.height, "length": args.length}}
        positive = ["21", 0]
        latent = ["21", 1]
        sampler_name = "euler"
        scheduler = "simple"

    nodes[str(nid)] = {"class_type": "KSampler", "inputs": {
        "model": model_ref, "positive": positive, "negative": positive,
        "latent_image": latent, "seed": args.seed, "steps": args.steps, "cfg": 1.0,
        "sampler_name": sampler_name, "scheduler": scheduler, "denoise": 1.0}}
    samp = str(nid)
    nid += 1
    nodes[str(nid)] = {"class_type": "VAEDecode", "inputs": {
        "samples": [samp, 0], "vae": ["3", 0]}}
    dec = str(nid)
    nid += 1
    nodes[str(nid)] = {"class_type": "CreateVideo", "inputs": {"images": [dec, 0], "fps": 24.0}}
    cv = str(nid)
    nid += 1
    nodes[str(nid)] = {"class_type": "SaveVideo", "inputs": {
        "video": [cv, 0], "filename_prefix": args.prefix, "format": "auto", "codec": "h264"}}
    return nodes


def wait_for_gpu(timeout: int = 1800) -> None:
    """Block until the PROD queue is empty (single GPU: renders must be serial)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            queue = requests.get(BASE + "/queue", timeout=15).json()
        except requests.RequestException:
            time.sleep(5)
            continue
        if not queue.get("queue_running") and not queue.get("queue_pending"):
            return
        time.sleep(5)
    raise TimeoutError("PROD queue did not drain")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--mode", choices=["fl2va", "ref2va"], default="fl2va")
    ap.add_argument("--turbo", action="store_true")
    ap.add_argument("--style", default="")
    ap.add_argument("--lora", action="store_true", help="apply the Realism People LoRA")
    ap.add_argument("--lora-strength", type=float, default=0.65)
    ap.add_argument("--width", type=int, default=704)
    ap.add_argument("--height", type=int, default=1056)
    ap.add_argument("--length", type=int, default=97)
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--prefix", default="h3skinprobe")
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--no-wait", action="store_true", help="skip the queue drain")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if not args.no_wait:
        wait_for_gpu()
    uploaded = upload(args.image)
    graph = build_graph(args, uploaded)
    (args.out_dir / f"{args.tag}.workflow.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")

    started = time.monotonic()
    response = requests.post(BASE + "/prompt", json={"prompt": graph, "client_id": "probe-h3"},
                             timeout=120)
    if response.status_code >= 400:
        raise RuntimeError(f"ComfyUI rejected the graph: {response.text[:600]}")
    prompt_id = response.json()["prompt_id"]
    print("prompt_id", prompt_id, flush=True)

    deadline = started + args.timeout
    while time.monotonic() < deadline:
        history = requests.get(BASE + f"/history/{prompt_id}", timeout=30).json()
        entry = history.get(prompt_id)
        if entry:
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(json.dumps(status.get("messages", []), ensure_ascii=False)[:800])
            for output in entry.get("outputs", {}).values():
                # ComfyUI reports a SaveVideo result under "images" with
                # metadata.kind == "video" (older cores used "videos").
                items = list(output.get("videos", [])) + list(output.get("images", []))
                for item in items:
                    source = PROD_OUT / item.get("subfolder", "") / item["filename"]
                    if not source.exists():
                        continue
                    if not source.name.lower().endswith(".mp4"):
                        continue
                    target = args.out_dir / f"{args.tag}.mp4"
                    shutil.copy2(source, target)
                    summary = {
                        "tag": args.tag, "mode": args.mode, "turbo": args.turbo,
                        "style": args.style, "lora": args.lora,
                        "lora_strength": args.lora_strength if args.lora else 0,
                        "width": args.width, "height": args.height,
                        "length": args.length, "steps": args.steps, "seed": args.seed,
                        "elapsed_seconds": round(time.monotonic() - started, 2),
                        "video": str(target),
                    }
                    (args.out_dir / f"{args.tag}.summary.json").write_text(
                        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
                    return 0
            print("history без видео:", json.dumps(entry.get("outputs", {}))[:300], flush=True)
            return 1
        time.sleep(5)
    raise TimeoutError(f"H3 probe {args.tag} exceeded {args.timeout}s")


if __name__ == "__main__":
    raise SystemExit(main())
