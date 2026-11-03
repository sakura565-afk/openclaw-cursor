#!/usr/bin/env python
"""Refine an existing video with ComfyUI-Tessera (hashu786).

Tessera is the only tool on this box that can add detail INSIDE one temporal
diffusion instead of frame by frame, which is the only shape of solution that
does not boil. It tiles the video spatially and temporally, keeps a guide
frame for identity, and re-denoises the H3 latent at the target resolution.

The graph mirrors the upstream example workflow (tessera_upscaler.json) with
the parts we do not need cut out: no SAM3 tracking (our clip has one
subject), no audio path (we mux the original audio back later).

    LoadVideo -> GetVideoComponents -> TesseraDilate
              -> MiniMaxH3ReferenceToVideo (video reference -> conditioning)
              -> TesseraEncode -> TesseraPlan -> TesseraRefine
              -> TesseraDecimate -> CreateVideo -> SaveVideo

Usage:
    python scripts/tessera_refine_video.py --video clip.mp4 --out out.mp4
    python scripts/tessera_refine_video.py --video clip.mp4 --plan-only
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_paths  # type: ignore  # noqa: E402

PROD = os.environ.get("COMFYUI_PROD", "http://127.0.0.1:8188")

# The prompt is the contract with the model: it says the reference video is
# reproduced one to one, so the refine pass must not invent new content.
TESSERA_PROMPT = """subject_definitions:
<Video 1>: the reference video. Its picture, motion, framing, timing and content are the target video's content.
<Picture 1> is a reference of a high resolution upscaled version of the first frame of the video.

retention_analysis:
<Video 1>: fully_preserved - <Video 1> is reproduced one to one as the target video. Every frame matches the source.

Enhance this video with sharp, crisp details while preserving a natural photorealistic appearance."""


def _get_json(url: str, timeout: int = 90) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "tessera"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post_json(url: str, payload: dict, timeout: int = 180) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # ComfyUI answers a bad graph with a per-node validation report; the
        # bare status code hides which input is wrong.
        body = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(body)
        except ValueError:
            detail = body
        raise RuntimeError(f"ComfyUI rejected the graph: {json.dumps(detail, ensure_ascii=False)[:2000]}") from exc


def upload_video(path: Path, server: str = PROD) -> str:
    """Copy the clip into ComfyUI's input dir and return its stored name."""
    dst = local_paths.PROD_IN / path.name
    if not dst.exists() or dst.stat().st_size != path.stat().st_size:
        shutil.copy2(path, dst)
    return path.name


def probe(server: str) -> dict:
    return _get_json(server + "/object_info", timeout=180)


def validate(server: str, needed: list[str]) -> list[str]:
    """Return the node classes that are missing on the running ComfyUI."""
    oi = probe(server)
    return [n for n in needed if n not in oi]


def build_graph(
    video_name: str,
    *,
    width: int,
    height: int,
    length: int,
    upscale_by: int,
    seed: int,
    cfg: float,
    spatial_tile: str,
    temporal_tile: str,
    tile_width: int,
    tile_height: int,
    temporal_size: int,
    temporal_overlap: int,
    context_border: int,
    seam_overlap: int,
    hero_min: int,
    hero_fit: str,
    segment_frames: int,
    prompt: str,
    prefix: str,
    fps: float,
    unet_name: str,
    clip_name: str,
    vae_name: str,
    lora_name: str = "",
    lora_strength: float = 0.0,
    anchor_strength: float = 1.0,
    anchor_spread: int = 4,
    anchor_name: str = "",
    plan_only: bool = False,
) -> dict:
    g: dict = {
        "1": {"class_type": "LoadVideo", "inputs": {"file": video_name}},
        "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
        "3": {
            "class_type": "TesseraDilate",
            "inputs": {
                "images": ["2", 0],
                "mode": "auto",
                "threshold": 1.2,
                "upscale_factor": float(upscale_by),
            },
        },
        "4": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet_name, "weight_dtype": "default"},
        },
        "5": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": clip_name, "type": "minimax"},
        },
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}},
        "7": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {
                "clip": ["5", 0],
                "prompt": prompt,
                "width": width,
                "height": height,
                "length": length,
                "ref_image_size": "match",
                "vae": ["6", 0],
                "ref_videos": [["1", 0]],
            },
        },
        "8": {
            "class_type": "LoadImage" if anchor_name else "ImageFromBatch",
            "inputs": ({"image": anchor_name} if anchor_name
                       else {"image": ["3", 0], "batch_index": 0, "length": 1}),
        },
        "9": {
            "class_type": "TesseraEncode",
            "inputs": {
                "images": ["3", 0],
                "vae": ["6", 0],
                "positive": ["7", 0],
                # The anchor is the identity source. The upstream recipe anchors
                # on the FIRST frame of the clip; a stronger still (our own
                # 9.2/10 portrait) anchors better, and the spread decides how
                # many frames around the anchor keep the identity constraint.
                "anchor": ["8", 0],
                "anchor_strength": anchor_strength,
                "anchor_spread": anchor_spread,
                "upscale_by": upscale_by,
                "upscale_method": "lanczos",
                "segment_frames": segment_frames,
            },
        },
        "10": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["7", 0]},
        },
        "11": {
            "class_type": "TesseraPlan",
            "inputs": {
                "width": ["9", 2],
                "height": ["9", 3],
                "spatial_tile": spatial_tile,
                "tile_width": tile_width,
                "tile_height": tile_height,
                "context_border": context_border,
                "hero_min": hero_min,
                "hero_fit": hero_fit,
                "frames": ["9", 4],
                "temporal_tile": temporal_tile,
                "temporal_size": temporal_size,
                "temporal_overlap": temporal_overlap,
                "diagram_width": 1200,
                "ref_tile": -1,
                "preview_image": ["8", 0],
                "positive": ["7", 0],
                "vae": ["6", 0],
                "frame_map": ["3", 3],
            },
        },
        "12": {
            "class_type": "TesseraArgs",
            "inputs": {
                "video_ref_crop": "per_tile",
                "image_ref_crop": "per_tile",
                "guide_noise": 0,
                "guide_noise_band": "full",
                "guide_noise_t": "per_frame",
                "guide_release": 0.8,
                "guide_inherit": True,
                "hero_guide_noise": -1,
                "motion_adapt": True,
                "motion_release_floor": 0.3,
                "fine_below_sigma": 0.75,
                "tile_select": "",
                "tile_limit": 0,
                "flat_skip": 4,
            },
        },
        "13": {
            "class_type": "CustomSigmas",
            "inputs": {"sigmas_string": "1, 0.7, 0.4, 0.0", "interpolate_to_steps": 3},
        },
        "14": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "er_sde"}},
    }

    model_src = "4"
    if lora_name:
        g["15"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["4", 0],
                "lora_name": lora_name,
                "strength_model": lora_strength,
            },
        }
        model_src = "15"

    g["20"] = {
        "class_type": "TesseraRefine",
        "inputs": {
            "model": [model_src, 0],
            "clip": ["5", 0],
            "positive": ["9", 0],
            "negative": ["10", 0],
            "latent": ["9", 1],
            "vae": ["6", 0],
            "sampler": ["14", 0],
            "sigmas": ["13", 0],
            "plan": ["11", 3],
            "args": ["12", 0],
            "frame_map": ["3", 3],
            "prompt": prompt,
            "spatial_tile": spatial_tile,
            "tile_width": tile_width,
            "tile_height": tile_height,
            "temporal_tile": temporal_tile,
            "temporal_size": temporal_size,
            "temporal_overlap": temporal_overlap,
            "context_border": context_border,
            "seam_overlap": seam_overlap,
            "hero_min": hero_min,
            "hero_fit": hero_fit,
            "cfg": cfg,
            "seed": seed,
            "seed_mode": "fixed",
            "pixel_out": True,
            "console_progress": "overall",
        },
    }

    if plan_only:
        # Cheap validation pass: only the plan runs, no sampling, no VRAM.
        g["21"] = {"class_type": "SaveImage", "inputs": {"images": ["11", 0], "filename_prefix": prefix + "_plan"}}
        return g

    g["22"] = {
        "class_type": "TesseraDecimate",
        "inputs": {"images": ["20", 2], "original_frames": ["3", 1]},
    }
    g["23"] = {"class_type": "CreateVideo", "inputs": {"images": ["22", 0], "fps": fps}}
    g["24"] = {
        "class_type": "SaveVideo",
        "inputs": {
            "video": ["23", 0],
            "filename_prefix": prefix,
            "format": "auto",
            "codec": "h264",
        },
    }
    return g


def wait_for(server: str, prompt_id: str, timeout: int = 7200) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            h = _get_json(server + "/history/" + prompt_id, timeout=90)
        except urllib.error.URLError:
            time.sleep(5)
            continue
        if prompt_id in h:
            entry = h[prompt_id]
            if "outputs" in entry:
                return entry
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(json.dumps(status, ensure_ascii=False)[:1200])
        time.sleep(5)
    raise TimeoutError(f"prompt {prompt_id} did not finish in {timeout}s")


def find_output(entry: dict) -> tuple[str, str]:
    """Return (subfolder, filename) of the produced file."""
    for node_out in entry.get("outputs", {}).values():
        for key in ("gifs", "videos", "images"):
            for item in node_out.get(key) or []:
                return item.get("subfolder", ""), item["filename"]
    raise RuntimeError("no output file in history entry")


def download(server: str, subfolder: str, filename: str, out: Path) -> Path:
    url = server + "/view?filename=" + filename.replace(" ", "%20")
    if subfolder:
        url += "&subfolder=" + subfolder.replace(" ", "%20")
    url += "&type=output"
    req = urllib.request.Request(url, headers={"User-Agent": "tessera"})
    out.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=1800) as r, open(out, "wb") as fh:
        shutil.copyfileobj(r, fh)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--width", type=int, default=0)
    ap.add_argument("--height", type=int, default=0)
    ap.add_argument("--length", type=int, default=0)
    ap.add_argument("--upscale-by", type=int, default=2)
    ap.add_argument("--seed", type=int, default=786110)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--fps", type=float, default=24.0)
    ap.add_argument("--spatial-tile", default="low_vram")
    ap.add_argument("--temporal-tile", default="low_vram")
    ap.add_argument("--tile-width", type=int, default=1024)
    ap.add_argument("--tile-height", type=int, default=1024)
    ap.add_argument("--temporal-size", type=int, default=32)
    ap.add_argument("--temporal-overlap", type=int, default=4)
    ap.add_argument("--context-border", type=int, default=64)
    ap.add_argument("--seam-overlap", type=int, default=32)
    ap.add_argument("--hero-min", type=int, default=896)
    ap.add_argument("--hero-fit", default="median")
    ap.add_argument("--segment-frames", type=int, default=56)
    ap.add_argument("--anchor-image", default="",
                    help="identity anchor still (our strongest portrait). Anchoring on a "
                         "9.2/10 still holds the face better than the clip's own first frame.")
    ap.add_argument("--anchor-strength", type=float, default=1.0)
    ap.add_argument("--anchor-spread", type=int, default=4,
                    help="how many frames around the anchor keep the identity constraint")
    ap.add_argument("--prefix", default="tessera")
    ap.add_argument("--prompt", default=TESSERA_PROMPT)
    ap.add_argument("--unet", default="minimax_h3_ref2va_pruned_int8_convrot.safetensors")
    ap.add_argument("--clip", default="qwen3vl_32b_minimax_h3_int8_convrot.safetensors")
    ap.add_argument("--vae", default="minimax_h3_video_vae_fp16.safetensors")
    ap.add_argument("--lora", default="")
    ap.add_argument("--lora-strength", type=float, default=0.0)
    ap.add_argument("--server", default=PROD)
    ap.add_argument("--timeout", type=int, default=7200)
    ap.add_argument("--plan-only", action="store_true")
    args = ap.parse_args()

    needed = [
        "LoadVideo", "GetVideoComponents", "TesseraDilate", "TesseraEncode",
        "TesseraPlan", "TesseraArgs", "TesseraRefine", "TesseraDecimate",
        "MiniMaxH3ReferenceToVideo", "CustomSigmas", "KSamplerSelect",
        "ConditioningZeroOut", "ImageFromBatch",
    ]
    missing = validate(args.server, needed)
    if missing:
        print("MISSING NODES (restart ComfyUI):", ", ".join(missing), flush=True)
        return 2

    video = Path(args.video)
    if not video.exists():
        print("video not found:", video, flush=True)
        return 2

    import subprocess

    probe_cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,nb_frames,r_frame_rate",
        "-of", "json", str(video),
    ]
    info = json.loads(subprocess.run(probe_cmd, capture_output=True, text=True, timeout=120).stdout)
    stream = (info.get("streams") or [{}])[0]
    width = args.width or int(stream.get("width") or 736)
    height = args.height or int(stream.get("height") or 1120)
    rate = stream.get("r_frame_rate") or "24/1"
    fps = float(rate.split("/")[0]) / max(1.0, float(rate.split("/")[1])) if "/" in rate else args.fps
    length = args.length or int(stream.get("nb_frames") or 0)
    if not length:
        length = int(subprocess.run(
            ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
             "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=600).stdout.strip() or "0")
    print(f"source: {width}x{height} {fps:.3f} fps {length} frames", flush=True)

    # Tessera works on a latent grid of 16, so both dimensions have to be a
    # multiple of 16 after the upscale factor is applied. Our own pipeline
    # emits heights like 1620 (1620*2 = 3240 = 16*202.5), which the model
    # refuses. Crop the tail of the frame instead of rescaling the picture.
    out_w = (width * args.upscale_by) // 16 * 16
    out_h = (height * args.upscale_by) // 16 * 16
    crop_w = out_w // args.upscale_by
    crop_h = out_h // args.upscale_by
    if (crop_w, crop_h) != (width, height):
        print(f"cropping to {crop_w}x{crop_h} (multiple of 16 after x{args.upscale_by})", flush=True)
        cropped = local_paths.SCRATCH / (video.stem + "_c16.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(video),
             "-vf", f"crop={crop_w}:{crop_h}", "-an", "-c:v", "libx264", "-crf", "16",
             "-pix_fmt", "yuv420p", str(cropped)],
            check=True, timeout=1800,
        )
        video = cropped
        width, height = crop_w, crop_h

    name = upload_video(video, args.server)
    anchor_name = ""
    if args.anchor_image:
        anchor_path = Path(args.anchor_image)
        dst = local_paths.PROD_IN / anchor_path.name
        if not dst.exists() or dst.stat().st_size != anchor_path.stat().st_size:
            shutil.copy2(anchor_path, dst)
        anchor_name = anchor_path.name
        print("anchor:", anchor_name, flush=True)
    graph = build_graph(
        name,
        width=width,
        height=height,
        length=length,
        upscale_by=args.upscale_by,
        seed=args.seed,
        cfg=args.cfg,
        spatial_tile=args.spatial_tile,
        temporal_tile=args.temporal_tile,
        tile_width=args.tile_width,
        tile_height=args.tile_height,
        temporal_size=args.temporal_size,
        temporal_overlap=args.temporal_overlap,
        context_border=args.context_border,
        seam_overlap=args.seam_overlap,
        hero_min=args.hero_min,
        hero_fit=args.hero_fit,
        segment_frames=args.segment_frames,
        prompt=args.prompt,
        prefix=args.prefix,
        fps=fps,
        unet_name=args.unet,
        clip_name=args.clip,
        vae_name=args.vae,
        lora_name=args.lora,
        lora_strength=args.lora_strength,
        anchor_strength=args.anchor_strength,
        anchor_spread=args.anchor_spread,
        anchor_name=anchor_name,
        plan_only=args.plan_only,
    )
    print("nodes:", len(graph), flush=True)
    res = _post_json(args.server + "/prompt", {"prompt": graph}, timeout=180)
    pid = res.get("prompt_id")
    print("prompt_id:", pid, flush=True)
    entry = wait_for(args.server, pid, timeout=args.timeout)
    sub, fn = find_output(entry)
    print("output:", sub, fn, flush=True)
    if args.plan_only:
        print("plan-only: output written by ComfyUI, no video produced", flush=True)
        return 0
    out = Path(args.out) if args.out else local_paths.SCRATCH / (args.prefix + ".mp4")
    download(args.server, sub, fn, out)
    print("saved:", out, out.stat().st_size, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
