#!/usr/bin/env python3
"""
ComfyUI video pipeline benchmark: RIFE (24→60), SUPIR (quality vs speed),
and RIFE+SUPIR on camera_motion_stable outputs.

Target hardware (documentation): NVIDIA RTX 3060 12GB. The script records the
actual GPU name from ComfyUI /system_stats when available.

Dependencies: requests, pillow, numpy, opencv-python-headless, ffmpeg on PATH.

Example:
  python scripts/comfy_video_benchmark.py --reference-image path/to/chair.jpg
  python scripts/comfy_video_benchmark.py --dry-run
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PIL import Image

from scripts.camera_motion_stable import render_sequence
from scripts.comfy_video_pipeline import (
    ComfyClient,
    ComfyVideoPipeline,
    MarkdownLogger,
    PipelineConfig,
    PipelineError,
)

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore

DEFAULT_RESULTS = Path("scripts/data/video_benchmark_results.md")
DEFAULT_MOTION_DIR = Path("scripts/data/camera_motion_stable_out")
DEFAULT_RIFE_DIR = Path("ComfyUI/models/frame_interpolation")
DEFAULT_COMFY_INPUT = Path("ComfyUI/input")
DEFAULT_SUPIR_CKPT = "SUPIR-v0Q_fp16.safetensors"
BENCH_LOG = Path("scripts/data/video_benchmark_log.md")


@dataclass
class BenchRow:
    model: str
    input_desc: str
    output_desc: str
    time_sec: float
    vram_peak_gb: float
    quality_score: float
    notes: str = ""


def luminance(rgb: np.ndarray) -> np.ndarray:
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def global_ssim_gray(a: np.ndarray, b: np.ndarray) -> float:
    """Single-window SSIM on grayscale float arrays in [0,1]."""
    c1, c2 = (0.01) ** 2, (0.03) ** 2
    mu1, mu2 = float(a.mean()), float(b.mean())
    s1, s2 = float(a.var()), float(b.var())
    sigma12 = float(((a - mu1) * (b - mu2)).mean())
    num = (2 * mu1 * mu2 + c1) * (2 * sigma12 + c2)
    den = (mu1 * mu1 + mu2 * mu2 + c1) * (s1 + s2 + c2)
    if den <= 1e-12:
        return 0.0
    return max(0.0, min(1.0, num / den))


def ssim_rgb_u8(a: np.ndarray, b: np.ndarray) -> float:
    ag = luminance(a.astype(np.float64) / 255.0)
    bg = luminance(b.astype(np.float64) / 255.0)
    return global_ssim_gray(ag, bg)


def laplacian_sharpness(rgb: np.ndarray) -> float:
    if cv2 is None:
        g = luminance(rgb.astype(np.float64))
        return float(np.var(np.gradient(g)[0]) + np.var(np.gradient(g)[1]))
    gray = np.asarray(luminance(rgb.astype(np.float64)), dtype=np.float64)
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    return float(lap.var())


def quality_from_sharpness_ratio(up: np.ndarray, base: np.ndarray) -> float:
    """0–100 heuristic: SUPIR detail vs bilinear upscale of same size."""
    from PIL import Image as PILImage

    w, h = up.shape[1], up.shape[0]
    small = PILImage.fromarray(base.astype(np.uint8)).resize((w, h), PILImage.Resampling.BILINEAR)
    bil = np.array(small)
    su = laplacian_sharpness(up)
    bb = laplacian_sharpness(bil)
    if bb < 1e-6:
        return min(100.0, su / 10.0)
    return float(max(0.0, min(100.0, 100.0 * su / (bb * 2.5))))


def run_ffmpeg(cmd: List[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def video_to_fps(src: Path, dst: Path, fps: int) -> None:
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vf",
            f"fps={fps}",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(dst),
        ]
    )


def video_to_upsampled_fps(src: Path, dst: Path, fps: int) -> None:
    """Naive FPS increase (frame duplication / timing) for baseline comparison."""
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vf",
            f"fps={fps}",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(dst),
        ]
    )


def extract_frames(video: Path, out_dir: Path, prefix: str = "f") -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{prefix}_*.png"):
        old.unlink()
    run_ffmpeg(["ffmpeg", "-y", "-i", str(video), str(out_dir / f"{prefix}_%05d.png")])
    return sorted(out_dir.glob(f"{prefix}_*.png"))


def mean_ssim_vs_ref(ref_frames: List[Path], test_frames: List[Path], max_pairs: int = 80) -> float:
    n = min(len(ref_frames), len(test_frames), max_pairs)
    if n < 2:
        return 0.0
    scores: List[float] = []
    for i in range(n):
        a = np.array(Image.open(ref_frames[i]).convert("RGB"))
        b = np.array(Image.open(test_frames[i]).convert("RGB"))
        if a.shape != b.shape:
            b = np.array(Image.fromarray(b).resize((a.shape[1], a.shape[0]), Image.Resampling.LANCZOS))
        scores.append(ssim_rgb_u8(a, b))
    return float(sum(scores) / len(scores))


def wait_prompt_profile_vram(client: ComfyClient, prompt_id: str, timeout_sec: int = 1800) -> Tuple[Dict[str, Any], float]:
    started = time.time()
    peak_gb = 0.0
    while time.time() - started < timeout_sec:
        try:
            resp = client.session.get(f"{client.base_url}/system_stats", timeout=15)
            resp.raise_for_status()
            stats = resp.json()
            for dev in stats.get("system", {}).get("devices", []):
                total = dev.get("vram_total") or 0
                free = dev.get("vram_free")
                if total and free is not None:
                    peak_gb = max(peak_gb, (total - free) / (1024**3))
        except Exception:
            pass
        hist = client._request("GET", f"/history/{prompt_id}").json()
        if prompt_id in hist:
            return hist[prompt_id], peak_gb
        time.sleep(0.35)
    raise PipelineError(f"Timeout waiting for prompt_id={prompt_id}")


def extract_first_image_ref(history_item: Dict[str, Any]) -> Optional[Dict[str, str]]:
    for node_data in history_item.get("outputs", {}).values():
        images = node_data.get("images", [])
        if images:
            return images[0]
    return None


def download_view_bytes(client: ComfyClient, image_ref: Dict[str, str]) -> bytes:
    params = {
        "filename": image_ref["filename"],
        "subfolder": image_ref.get("subfolder", ""),
        "type": image_ref.get("type", "output"),
    }
    return client._request("GET", "/view", params=params).content


def extract_first_video_ref(history_item: Dict[str, Any]) -> Optional[Dict[str, str]]:
    for node_data in history_item.get("outputs", {}).values():
        videos = node_data.get("gifs", []) or node_data.get("videos", [])
        if videos:
            return videos[0]
    return None


def list_rife_models(rife_dir: Path) -> List[str]:
    if not rife_dir.exists():
        return ["rife47"]
    names: List[str] = []
    for p in sorted(rife_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in {".pth", ".onnx", ".pt"}:
            names.append(p.name)
        elif p.is_dir() and not p.name.startswith("."):
            names.append(p.name)
    return names or ["rife47"]


def build_supir_workflow(
    object_info: Dict[str, Any],
    image_name: str,
    scale: float,
    ckpt_name: str,
) -> Optional[Dict[str, Any]]:
    supir_node = next((c for c in ("SUPIR_Upscale", "SUPIR") if c in object_info), None)
    if not supir_node:
        return None

    req = (object_info.get(supir_node, {}).get("input") or {}).get("required") or {}
    img_key = "pixels" if "pixels" in req and "image" not in req else "image"
    inputs: Dict[str, Any] = {img_key: ["1", 0]}
    if "scale" in req:
        inputs["scale"] = scale

    for key, spec in req.items():
        if key in (img_key, "scale"):
            continue
        lk = key.lower()
        if not any(x in lk for x in ("model", "ckpt", "sdxl", "supir", "checkpoint")):
            continue
        choices: Optional[List[Any]] = None
        if isinstance(spec, (list, tuple)) and spec and isinstance(spec[0], list):
            choices = list(spec[0])
        if choices is None:
            continue
        if ckpt_name in choices:
            inputs[key] = ckpt_name
        else:
            preferred = next((c for c in choices if "SUPIR" in str(c).upper()), None)
            if preferred is not None:
                inputs[key] = preferred
            elif choices:
                inputs[key] = choices[0]

    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name, "upload": "image"}},
        "2": {"class_type": supir_node, "inputs": inputs},
        "3": {
            "class_type": "SaveImage",
            "inputs": {"images": ["2", 0], "filename_prefix": "bench_supir"},
        },
    }


def discover_gpu_name(client: ComfyClient) -> str:
    try:
        stats = client.session.get(f"{client.base_url}/system_stats", timeout=15).json()
        names = [d.get("name", "") for d in stats.get("system", {}).get("devices", [])]
        return ", ".join(n for n in names if n) or "unknown"
    except Exception:
        return "unknown"


def write_results_md(
    path: Path,
    rows: List[BenchRow],
    gpu_reported: str,
    rife_baseline_dup_ssim: Optional[float],
    furniture_settings_md: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "# ComfyUI video pipeline benchmark",
        "",
        f"_Generated: {ts}_",
        "",
        "## Hardware",
        "",
        f"- **Documented target:** NVIDIA RTX 3060 12GB VRAM (furniture catalog videos).",
        f"- **ComfyUI /system_stats devices:** {gpu_reported}",
        "",
        "## Summary tables",
        "",
        "| model | input | output | time_sec | vram_peak_gb | quality_score |",
        "|-------|-------|--------|----------|--------------|---------------|",
    ]
    for r in rows:
        lines.append(
            f"| {r.model} | {r.input_desc} | {r.output_desc} | {r.time_sec:.2f} | "
            f"{r.vram_peak_gb:.2f} | {r.quality_score:.2f} |"
        )
    if any(r.notes for r in rows):
        lines.extend(["", "#### Row notes", ""])
        for r in rows:
            if r.notes:
                lines.append(f"- **{r.model}:** {r.notes}")
    lines.extend(
        [
            "",
            "### Metric notes",
            "",
            "- **RIFE rows:** `quality_score` = mean SSIM×100 between benchmark output frames and the **60 FPS reference** "
            "from `camera_motion_stable` (same motion, no neural interpolation). Higher is closer to the reference.",
            "- **Naive 24→60 baseline:** duplicate/timed frames via ffmpeg `fps=60`; mean SSIM×100 vs reference is reported below when measured.",
            "- **SUPIR rows:** `quality_score` = heuristic 0–100 from Laplacian sharpness vs bilinear upscale to the same pixel size (detail gain proxy).",
            "",
        ]
    )
    if rife_baseline_dup_ssim is not None:
        lines.append(
            f"- **Naive ffmpeg 24→60 SSIM×100 (baseline):** {rife_baseline_dup_ssim:.2f}",
        )
        lines.append("")
    lines.extend(
        [
            "## Best settings for furniture video (RTX 3060 12GB)",
            "",
            furniture_settings_md,
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def furniture_settings_block() -> str:
    return "\n".join(
        [
            "| Stage | Setting | Rationale |",
            "|-------|---------|-----------|",
            "| **Capture / motion** | `camera_motion_stable.py --effect pan --curve smoothstep --fps 60` | Smooth easing reduces judder; pan showcases depth on cabinets/sofas without extreme perspective drift. |",
            "| **Base resolution** | 768×768 or 832×832 img2img before RIFE | Fits comfortably in 12GB with SUPIR headroom; raise only if SUPIR is off. |",
            "| **RIFE** | Target **60 FPS**; source catalog motion at **24 FPS** is fine if RIFE runs last on baked MP4 | 24→60 improves motion continuity for web/social; use models from `models/frame_interpolation/`. |",
            "| **SUPIR** | `SUPIR-v0Q_fp16.safetensors`, scale **1.25–1.5×** first | Sweet spot on 3060 12GB for furniture textures; 2× only for hero shots or shorter clips. |",
            "| **SD img2img (when used)** | steps **24–32**, denoise **0.28–0.38**, CFG **5.5–7** | Preserves wood grain/upholstery; higher denoise blurs fine veneer lines. |",
            "| **VRAM guard** | Run SUPIR after RIFE on **short segments** or **tile** if OOM | Peak memory spikes on high-res SUPIR; keep batch frame lists small. |",
        ]
    )


def run_rife_job(
    client: ComfyClient,
    pipe: ComfyVideoPipeline,
    object_info: Dict[str, Any],
    input_video_name: str,
    model_name: str,
    target_fps: int,
    work_dir: Path,
) -> Tuple[Path, float, float]:
    workflow = pipe._build_rife_workflow_dynamic(
        object_info=object_info,
        input_video=input_video_name,
        model_name=model_name,
        target_fps=target_fps,
    )
    if workflow is None:
        raise PipelineError("RIFE workflow could not be built from object_info.")
    t0 = time.perf_counter()
    pid = client.queue_prompt(workflow)
    hist, peak = wait_prompt_profile_vram(client, pid, timeout_sec=2400)
    elapsed = time.perf_counter() - t0
    refv = extract_first_video_ref(hist)
    if not refv:
        raise PipelineError("RIFE produced no video output.")
    out = work_dir / f"rife_out_{model_name.replace('/', '_')}.mp4"
    client.download_view_image(
        filename=refv["filename"],
        subfolder=refv.get("subfolder", ""),
        folder_type=refv.get("type", "output"),
        out_path=out,
    )
    return out, elapsed, peak


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark ComfyUI RIFE + SUPIR video pipeline.")
    parser.add_argument("--comfy-url", default=os.environ.get("COMFY_URL", "http://127.0.0.1:8188"))
    parser.add_argument("--reference-image", type=Path, help="Furniture photo for synthetic motion (required unless --dry-run).")
    parser.add_argument("--motion-out-dir", type=Path, default=DEFAULT_MOTION_DIR)
    parser.add_argument("--rife-models-dir", type=Path, default=Path(os.environ.get("COMFYUI_MODELS_FRAME", str(DEFAULT_RIFE_DIR))))
    parser.add_argument("--comfy-input-dir", type=Path, default=Path(os.environ.get("COMFYUI_INPUT", str(DEFAULT_COMFY_INPUT))))
    parser.add_argument("--results-md", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--supir-ckpt", default=os.environ.get("SUPIR_CKPT", DEFAULT_SUPIR_CKPT))
    parser.add_argument("--dry-run", action="store_true", help="Skip ComfyUI; write template markdown only.")
    parser.add_argument("--skip-supir", action="store_true")
    parser.add_argument("--skip-rife", action="store_true")
    parser.add_argument("--max-rife-models", type=int, default=3, help="Cap RIFE models scanned (safety).")
    parser.add_argument("--supir-scales", default="1.25,1.5,2.0", help="Comma-separated SUPIR scale factors.")
    args = parser.parse_args()

    logger = MarkdownLogger(BENCH_LOG)
    rows: List[BenchRow] = []
    baseline_ssim: Optional[float] = None
    gpu_name = "unknown (dry-run)"

    if args.dry_run:
        rows.append(
            BenchRow(
                model="(dry-run)",
                input_desc="—",
                output_desc="—",
                time_sec=0.0,
                vram_peak_gb=0.0,
                quality_score=0.0,
                notes="no ComfyUI calls",
            )
        )
        write_results_md(args.results_md, rows, gpu_name, None, furniture_settings_block())
        print(f"[OK] Wrote {args.results_md} (dry-run)")
        return 0

    if not args.reference_image or not args.reference_image.exists():
        print("[ERROR] --reference-image required (existing file) when not using --dry-run", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="comfy_vid_bench_") as tmp:
        tmp_path = Path(tmp)
        motion_dir = tmp_path / "motion"
        render_sequence(
            args.reference_image,
            motion_dir,
            width=768,
            height=768,
            duration=2.0,
            fps=60,
            effect="pan",
            curve="smoothstep",
            light_swivel=0.04,
        )
        ref_mp4 = motion_dir / "stable_motion.mp4"
        ref_frames_dir = tmp_path / "ref_frames"
        ref_frames = extract_frames(ref_mp4, ref_frames_dir, "ref")

        vid_24 = tmp_path / "stable_24fps.mp4"
        video_to_fps(ref_mp4, vid_24, 24)

        naive_60 = tmp_path / "naive_60_from_24.mp4"
        video_to_upsampled_fps(vid_24, naive_60, 60)
        naive_dir = tmp_path / "naive_frames"
        naive_frames = extract_frames(naive_60, naive_dir, "nv")
        baseline_ssim = mean_ssim_vs_ref(ref_frames, naive_frames) * 100.0

        client = ComfyClient(args.comfy_url, retries=3, backoff=1.0, logger=logger)
        try:
            client.ping()
        except Exception as exc:  # noqa: BLE001
            logger.log("benchmark ping failed", str(exc))
            rows.append(
                BenchRow(
                    model="ComfyUI",
                    input_desc="ping",
                    output_desc="—",
                    time_sec=0.0,
                    vram_peak_gb=0.0,
                    quality_score=0.0,
                    notes=f"unreachable: {exc}",
                )
            )
            write_results_md(
                args.results_md, rows, "ComfyUI unreachable", baseline_ssim, furniture_settings_block()
            )
            print(f"[WARN] ComfyUI unreachable; wrote partial {args.results_md}")
            return 2

        gpu_name = discover_gpu_name(client)
        object_info = client.get_object_info()

        dummy_img = tmp_path / "dummy_in.png"
        Image.new("RGB", (64, 64), color=(110, 95, 80)).save(dummy_img)
        cfg = PipelineConfig(
            input_image=dummy_img,
            effect="pan",
            duration=2.0,
            fps=60,
            frames_dir=tmp_path / "pipe_frames",
            comfy_url=args.comfy_url,
            rife_models_dir=str(args.rife_models_dir),
        )
        pipe = ComfyVideoPipeline(cfg)

        comfy_in = args.comfy_input_dir
        comfy_in.mkdir(parents=True, exist_ok=True)
        v24_name = "bench_stable_24.mp4"
        shutil.copy2(vid_24, comfy_in / v24_name)

        rife_models = list_rife_models(args.rife_models_dir)[: max(1, args.max_rife_models)]

        if not args.skip_rife:
            for mname in rife_models:
                try:
                    out_mp4, elapsed, peak = run_rife_job(
                        client, pipe, object_info, v24_name, mname, 60, tmp_path
                    )
                    out_fr = tmp_path / f"frames_{mname.replace('/', '_')}"
                    tst = extract_frames(out_mp4, out_fr, "t")
                    q = mean_ssim_vs_ref(ref_frames, tst) * 100.0
                    rows.append(
                        BenchRow(
                            model=f"RIFE:{mname}",
                            input_desc="24fps MP4 (camera_motion_stable)",
                            output_desc="60fps MP4 (ComfyUI RIFE)",
                            time_sec=elapsed,
                            vram_peak_gb=peak,
                            quality_score=q,
                            notes=f"vs 60fps ref; naive baseline SSIM×100={baseline_ssim:.1f}",
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    rows.append(
                        BenchRow(
                            model=f"RIFE:{mname}",
                            input_desc=v24_name,
                            output_desc="—",
                            time_sec=0.0,
                            vram_peak_gb=0.0,
                            quality_score=0.0,
                            notes=f"error: {exc}",
                        )
                    )

        scales = [float(x.strip()) for x in args.supir_scales.split(",") if x.strip()]
        mid = ref_frames[len(ref_frames) // 2]
        wf_tpl: Optional[Dict[str, Any]] = None
        if not args.skip_supir:
            upload = client.upload_image(mid)
            in_name = upload.get("name") or mid.name
            wf_tpl = build_supir_workflow(object_info, in_name, 1.5, args.supir_ckpt)
            if wf_tpl is None:
                rows.append(
                    BenchRow(
                        model=f"SUPIR:{args.supir_ckpt}",
                        input_desc=mid.name,
                        output_desc="—",
                        time_sec=0.0,
                        vram_peak_gb=0.0,
                        quality_score=0.0,
                        notes="no SUPIR node in object_info",
                    )
                )
            else:
                for sc in scales:
                    wf = json.loads(json.dumps(wf_tpl))  # deep copy via JSON
                    for nid, node in wf.items():
                        if node.get("class_type") in ("SUPIR_Upscale", "SUPIR"):
                            if "scale" in node.get("inputs", {}):
                                node["inputs"]["scale"] = sc
                    try:
                        t0 = time.perf_counter()
                        pid = client.queue_prompt(wf)
                        hist, peak = wait_prompt_profile_vram(client, pid, timeout_sec=1200)
                        elapsed = time.perf_counter() - t0
                        ref_img = extract_first_image_ref(hist)
                        if not ref_img:
                            raise PipelineError("no SUPIR image")
                        raw = download_view_bytes(client, ref_img)
                        up_arr = np.array(Image.open(io.BytesIO(raw)).convert("RGB"))
                        base_arr = np.array(Image.open(mid).convert("RGB"))
                        q = quality_from_sharpness_ratio(up_arr, base_arr)
                        rows.append(
                            BenchRow(
                                model=f"SUPIR:{args.supir_ckpt}",
                                input_desc=f"{mid.name} ({base_arr.shape[1]}×{base_arr.shape[0]})",
                                output_desc=f"upscaled scale={sc}",
                                time_sec=elapsed,
                                vram_peak_gb=peak,
                                quality_score=q,
                                notes="sharpness vs bilinear",
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        rows.append(
                            BenchRow(
                                model=f"SUPIR:{args.supir_ckpt}",
                                input_desc=mid.name,
                                output_desc=f"scale={sc}",
                                time_sec=0.0,
                                vram_peak_gb=0.0,
                                quality_score=0.0,
                                notes=f"error: {exc}",
                            )
                        )

        # RIFE + SUPIR on motion-stable output (subset of frames)
        if not args.skip_rife and not args.skip_supir and rows and wf_tpl is not None:
            try:
                chain_dir = tmp_path / "chain_frames"
                candidates = list(tmp_path.glob("rife_out_*.mp4"))
                if candidates:
                    last_rife_mp4 = max(candidates, key=lambda p: p.stat().st_mtime)
                    frs = extract_frames(last_rife_mp4, chain_dir, "c")[:8]
                    chain_scores: List[float] = []
                    chain_time = 0.0
                    chain_peak = 0.0
                    for fr in frs:
                        up_info = client.upload_image(fr)
                        inm = up_info.get("name") or fr.name
                        wf_c = build_supir_workflow(object_info, inm, 1.5, args.supir_ckpt)
                        if wf_c is None:
                            break
                        for nid, node in wf_c.items():
                            if node.get("class_type") in ("SUPIR_Upscale", "SUPIR"):
                                if "scale" in node.get("inputs", {}):
                                    node["inputs"]["scale"] = 1.5
                        t0 = time.perf_counter()
                        pid = client.queue_prompt(wf_c)
                        hist, peak = wait_prompt_profile_vram(client, pid, timeout_sec=1200)
                        chain_time += time.perf_counter() - t0
                        chain_peak = max(chain_peak, peak)
                        ref_img = extract_first_image_ref(hist)
                        if ref_img:
                            raw = download_view_bytes(client, ref_img)
                            up_arr = np.array(Image.open(io.BytesIO(raw)).convert("RGB"))
                            base_arr = np.array(Image.open(fr).convert("RGB"))
                            chain_scores.append(quality_from_sharpness_ratio(up_arr, base_arr))
                    if chain_scores:
                        rows.append(
                            BenchRow(
                                model="RIFE+SUPIR pipeline",
                                input_desc="camera_motion_stable 24→60 then SUPIR 1.5×",
                                output_desc=f"{len(chain_scores)} frames",
                                time_sec=chain_time,
                                vram_peak_gb=chain_peak,
                                quality_score=float(sum(chain_scores) / len(chain_scores)),
                                notes="SUPIR sharpness heuristic on RIFE output",
                            )
                        )
            except Exception as exc:  # noqa: BLE001
                rows.append(
                    BenchRow(
                        model="RIFE+SUPIR pipeline",
                        input_desc="motion stable",
                        output_desc="—",
                        time_sec=0.0,
                        vram_peak_gb=0.0,
                        quality_score=0.0,
                        notes=f"error: {exc}",
                    )
                )

    write_results_md(args.results_md, rows, gpu_name, baseline_ssim, furniture_settings_block())
    print(f"[OK] Wrote {args.results_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
