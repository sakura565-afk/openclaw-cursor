# -*- coding: utf-8 -*-
"""Controlled FreeVideo skin-texture probe; never changes the production graph."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from video_pipeline.config import FreeVideoConfig, OutputSpec
from video_pipeline.directors.freevideo import FreeVideoDirector
from video_pipeline.vram_guard import acquire_render_lock, release_render_lock

PROMPT = """r34l1sm, documentary photorealistic live-action footage of the same adult woman in a white bathtub in a rundown bathroom, natural damp skin with fine pores, subtle pigment variation and fine vellus hair; soft directional light, no beauty retouching. She breathes, blinks and moves her fingers naturally. Static camera, continuous shot, quiet water and room tone, no dialogue."""


def gpu_sample() -> dict[str, float]:
    proc = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    try:
        used, util = proc.stdout.strip().splitlines()[0].split(",")
        return {"used_mib": float(used), "util_pct": float(util)}
    except (ValueError, IndexError):
        return {"used_mib": 0.0, "util_pct": 0.0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--tier", choices=["light", "medium", "high", "max"], default="high")
    ap.add_argument("--width", type=int, default=1344)
    ap.add_argument("--height", type=int, default=768)
    ap.add_argument("--seed", type=int, default=73571)
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--frames", type=int, default=124)
    ap.add_argument("--lora", default="")
    ap.add_argument("--lora-strength", type=float, default=0.65)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.out_dir / f"{args.tag}.mp4"
    if args.tier == "light":
        two_pass, steps, cache, upscale = True, 8, "", 2.0
    elif args.tier == "medium":
        two_pass, steps, cache, upscale = True, 8, "", 1.5
    else:
        two_pass, steps, cache, upscale = False, 16 if args.tier == "high" else 20, "easycache-av", 2.0
    cfg = FreeVideoConfig(
        width=args.width, height=args.height, seconds=args.seconds, seed=args.seed,
        two_pass=two_pass, base_steps=steps, refine_steps=3, step_cache=cache,
        upscale_factor=upscale, frame_lock=False,
    )
    director = FreeVideoDirector(args.out_dir, freevideo_config=cfg, free_vram_before_submit=True)
    spec = OutputSpec(format="16:9", motion="static", duration_sec=int(args.seconds))
    acquire_render_lock(f"probe_skin_render {args.tag}")
    stop = threading.Event()
    samples: list[dict[str, float]] = []

    def monitor() -> None:
        while not stop.wait(2.0):
            samples.append(gpu_sample())

    thread = threading.Thread(target=monitor, daemon=True)
    started = time.monotonic()
    prompt_id = ""
    try:
        job = director.prepare(args.source, spec)
        job.output_path = output_path
        job.prompt_text = PROMPT
        job.workflow["2"]["inputs"]["text"] = PROMPT
        job.workflow["2"]["inputs"]["frames"] = args.frames
        job.workflow["2"]["inputs"]["force_regenerate"] = True
        if args.lora:
            job.workflow["10"] = {
                "class_type": "FreeVideoLoRA",
                "inputs": {"lora": args.lora, "strength": args.lora_strength},
            }
            job.workflow["2"]["inputs"]["loras"] = ["10", 0]
        (args.out_dir / f"{args.tag}.workflow.json").write_text(
            json.dumps(job.workflow, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        thread.start()
        prompt_id = director.submit(job)
        result = director.poll_until_done_with_job(prompt_id, args.timeout, job)
        elapsed = time.monotonic() - started
        report = None
        for node_output in result.raw_outputs.get("outputs", {}).values():
            for key in ("report", "text", "string"):
                value = node_output.get(key)
                raw = value[0] if isinstance(value, list) and value else value
                if isinstance(raw, str) and raw.strip().startswith("{"):
                    report = json.loads(raw)
                    break
            if report is not None:
                break
        if report is not None:
            (args.out_dir / f"{args.tag}.request.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        frame = args.out_dir / f"{args.tag}_f000.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(output_path), "-frames:v", "1", str(frame)], check=True, timeout=60)
        summary = {
            "tag": args.tag, "tier": args.tier, "width": args.width, "height": args.height,
            "frames_requested": args.frames, "seed": args.seed, "lora": args.lora,
            "lora_strength": args.lora_strength if args.lora else 0,
            "prompt_id": prompt_id, "elapsed_seconds": round(elapsed, 2),
            "video": str(output_path), "first_frame": str(frame),
            "gpu_peak_used_mib": max((s["used_mib"] for s in samples), default=0),
            "report_vram_peak_bytes": (report or {}).get("vram_peak_bytes"),
            "report_ram_peak_bytes": (report or {}).get("ram_peak_bytes"),
            "video_duration_seconds": result.duration_sec,
        }
        (args.out_dir / f"{args.tag}.summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    except Exception:
        if prompt_id:
            try:
                requests.post("http://127.0.0.1:8189/interrupt", timeout=10)
            except requests.RequestException:
                pass
        raise
    finally:
        stop.set()
        if thread.is_alive():
            thread.join(timeout=4)
        release_render_lock()


if __name__ == "__main__":
    raise SystemExit(main())
