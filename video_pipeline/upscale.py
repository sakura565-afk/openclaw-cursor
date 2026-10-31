"""Pluggable video upscale backends (SeedVR2, ESRGAN, Lanczos) via ComfyUI HTTP or ffmpeg."""

from __future__ import annotations

import copy
import os
import time
from pathlib import Path
from typing import Any, Callable, Literal

import requests
from loguru import logger

from video_pipeline.assembler import (
    AssemblyError,
    _download_first_video,
    _run_ffmpeg,
    _submit_prompt,
    _upload_video,
    probe,
    scale_pad_filter,
)
from video_pipeline.config import UpscaleConfig

BackendName = Literal["seedvr2", "esrgan", "lanczos"]

POLL_INTERVAL_SEC = 2.0
# A SeedVR2 upscale of a 173-frame clip takes ~20-25 min on a 16 GB box, and up to
# ~2 h when the GPU is shared (the VAE tiled-decode step grows from ~6 s to ~270 s
# per batch). 3600 s was short enough that a slow run silently timed out.
POLL_TIMEOUT_SEC = 14400

# Environment switches (see the "Silent fallback" section of the module docstring):
#   UPSCALE_POLL_TIMEOUT   - override POLL_TIMEOUT_SEC (seconds).
#   UPSCALE_NO_FALLBACK=1  - raise instead of silently substituting Lanczos when the
#                            learned backend fails, times out or is unavailable.
_ENV_TIMEOUT = "UPSCALE_POLL_TIMEOUT"
_ENV_NO_FALLBACK = "UPSCALE_NO_FALLBACK"

SEEDVR2_DIT_MODEL = "seedvr2_ema_3b_fp8_e4m3fn.safetensors"
SEEDVR2_VAE_MODEL = "ema_vae_fp16.safetensors"
ESRGAN_MODEL = "4x-UltraSharp.pth"

_BACKEND_ORDER: tuple[BackendName, ...] = ("seedvr2", "esrgan", "lanczos")


class UpscaleError(RuntimeError):
    """Raised when upscale operations fail."""


def _vhs_load_video_inputs(video_filename: str) -> dict[str, Any]:
    return {
        "video": video_filename,
        "force_rate": 0,
        "force_size": "Disabled",
        "custom_width": 0,
        "custom_height": 0,
        "frame_load_cap": 0,
        "skip_first_frames": 0,
        "select_every_nth": 1,
    }


def _vhs_combine_inputs(
    images_node: tuple[str, int],
    audio_node: tuple[str, int] | None,
    frame_rate: float,
    filename_prefix: str,
) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "images": list(images_node),
        "frame_rate": frame_rate,
        "loop_count": 0,
        "filename_prefix": filename_prefix,
        "format": "video/h264-mp4",
        "pingpong": False,
        "save_output": True,
    }
    if audio_node is not None:
        inputs["audio"] = list(audio_node)
    return inputs


def build_seedvr2_workflow(
    video_filename: str,
    frame_rate: float,
    config: UpscaleConfig,
    *,
    seed: int = 42,
) -> dict[str, Any]:
    """Build the verified SeedVR2 ComfyUI API workflow."""
    return {
        "1": {
            "class_type": "VHS_LoadVideo",
            "inputs": _vhs_load_video_inputs(video_filename),
        },
        "2": {
            "class_type": "SeedVR2LoadDiTModel",
            "inputs": {
                "model": SEEDVR2_DIT_MODEL,
                "device": "cuda:0",
                "blocks_to_swap": config.blocks_to_swap,
                "swap_io_components": True,
                "offload_device": "cpu",
                "cache_model": False,
                "attention_mode": "sdpa",
            },
        },
        "3": {
            "class_type": "SeedVR2LoadVAEModel",
            "inputs": {
                "model": SEEDVR2_VAE_MODEL,
                "device": "cuda:0",
                "encode_tiled": True,
                "encode_tile_size": 1024,
                "encode_tile_overlap": 128,
                "decode_tiled": True,
                "decode_tile_size": 1024,
                "decode_tile_overlap": 128,
                "tile_debug": "false",
                "offload_device": "none",
                "cache_model": False,
            },
        },
        "4": {
            "class_type": "SeedVR2VideoUpscaler",
            "inputs": {
                "image": ["1", 0],
                "dit": ["2", 0],
                "vae": ["3", 0],
                "seed": seed,
                "resolution": config.resolution,
                "max_resolution": 0,
                "batch_size": config.batch_size,
                "uniform_batch_size": False,
                "color_correction": "lab",
                "temporal_overlap": config.temporal_overlap,
                "prepend_frames": 0,
                "input_noise_scale": 0.0,
                "latent_noise_scale": 0.0,
                "offload_device": "cpu",
                "enable_debug": False,
            },
        },
        "5": {
            "class_type": "VHS_VideoCombine",
            "inputs": _vhs_combine_inputs(
                ("4", 0),
                ("1", 2),
                frame_rate,
                "seedvr2",
            ),
        },
    }


def build_esrgan_workflow(video_filename: str, frame_rate: float) -> dict[str, Any]:
    """Build ComfyUI workflow: VHS load -> 4x-UltraSharp -> combine with source audio."""
    return {
        "1": {
            "class_type": "VHS_LoadVideo",
            "inputs": _vhs_load_video_inputs(video_filename),
        },
        "2": {
            "class_type": "UpscaleModelLoader",
            "inputs": {"model_name": ESRGAN_MODEL},
        },
        "3": {
            "class_type": "ImageUpscaleWithModel",
            "inputs": {
                "upscale_model": ["2", 0],
                "image": ["1", 0],
            },
        },
        "4": {
            "class_type": "VHS_VideoCombine",
            "inputs": _vhs_combine_inputs(
                ("3", 0),
                ("1", 2),
                frame_rate,
                "esrgan_upscale",
            ),
        },
    }


def capabilities_from_object_info(object_info: dict[str, Any]) -> set[BackendName]:
    """Derive which learned backends are available from a ComfyUI /object_info payload."""
    caps: set[BackendName] = set()
    if "SeedVR2VideoUpscaler" in object_info:
        caps.add("seedvr2")
    if "UpscaleModelLoader" in object_info and "ImageUpscaleWithModel" in object_info:
        caps.add("esrgan")
    return caps


def select_backend(
    config: UpscaleConfig,
    object_info: dict[str, Any] | None,
) -> BackendName:
    """Resolve backend from config and optional object_info (for ``auto`` and validation)."""
    caps = capabilities_from_object_info(object_info or {})
    if config.backend == "auto":
        for name in _BACKEND_ORDER:
            if name == "lanczos":
                return "lanczos"
            if name in caps:
                return name
        return "lanczos"

    chosen = config.backend
    if chosen == "lanczos":
        return "lanczos"
    if chosen in caps:
        return chosen
    return "lanczos"


def fetch_object_info(
    comfyui_url: str,
    request_func: Callable[..., Any] = requests.get,
) -> dict[str, Any] | None:
    """Fetch full /object_info from ComfyUI; return None on failure."""
    url = f"{comfyui_url.rstrip('/')}/object_info"
    try:
        response = request_func(url, timeout=15)
        if response.status_code >= 400:
            return None
        data = response.json()
        return data if isinstance(data, dict) else None
    except (requests.RequestException, ValueError):
        return None


def release_vram(
    urls: list[str],
    *,
    request_func_post: Callable[..., Any] = requests.post,
) -> list[str]:
    """POST ``/free`` on each ComfyUI base URL to unload models and free GPU memory."""
    payload = {"unload_models": True, "free_memory": True}
    succeeded: list[str] = []
    for url in urls:
        try:
            request_func_post(
                f"{url.rstrip('/')}/free",
                json=payload,
                timeout=30,
            )
            succeeded.append(url)
        except Exception as exc:
            logger.warning("Failed to release VRAM at {}: {}", url, exc)
    return succeeded


def free_vram_gb(
    url: str,
    *,
    request_func_get: Callable[..., Any] = requests.get,
) -> float | None:
    """Return free VRAM in GB from ComfyUI ``/system_stats``, or None if unavailable."""
    try:
        response = request_func_get(f"{url.rstrip('/')}/system_stats", timeout=15)
        if response.status_code >= 400:
            return None
        data = response.json()
        if not isinstance(data, dict):
            return None
        devices = data.get("devices")
        if not isinstance(devices, list) or not devices:
            return None
        device0 = devices[0]
        if not isinstance(device0, dict):
            return None
        vram_free = device0.get("vram_free")
        if vram_free is None:
            return None
        return float(vram_free) / (1024**3)
    except (requests.RequestException, ValueError, TypeError):
        return None


def _comfy_reachable(comfyui_url: str, request_func: Callable[..., Any] = requests.get) -> bool:
    try:
        response = request_func(f"{comfyui_url.rstrip('/')}/system_stats", timeout=5)
        return response.status_code < 500
    except requests.RequestException:
        return False


def lanczos_upscale(in_path: Path, out_path: Path, width: int, height: int) -> Path:
    """ffmpeg Lanczos scale+pad; copies audio when present."""
    in_path = Path(in_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    info = probe(in_path)
    vf = scale_pad_filter(width, height)
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(in_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
    ]
    if info["has_audio"]:
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-an"]
    cmd.append(str(out_path))
    _run_ffmpeg(cmd, "lanczos_upscale")
    return out_path


def mux_audio_from_source(video_path: Path, audio_source: Path, out_path: Path) -> Path:
    """Copy video stream from ``video_path`` and audio from ``audio_source``."""
    video_path = Path(video_path)
    audio_source = Path(audio_source)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    src_info = probe(audio_source)
    if not src_info["has_audio"]:
        if video_path != out_path:
            out_path.write_bytes(video_path.read_bytes())
        return out_path
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(video_path),
        "-i",
        str(audio_source),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0?",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(out_path),
    ]
    _run_ffmpeg(cmd, "mux_audio_from_source")
    return out_path


def apply_target_box(in_path: Path, out_path: Path, width: int, height: int) -> Path:
    """Scale+pad to exact target dimensions, preserving audio from ``in_path`` when possible."""
    return lanczos_upscale(in_path, out_path, width, height)


def _poll_timeout_sec() -> float:
    """Poll timeout in seconds: ``UPSCALE_POLL_TIMEOUT`` overrides the module default."""
    raw = os.environ.get(_ENV_TIMEOUT)
    if raw:
        try:
            value = float(raw)
        except ValueError:
            logger.warning("Invalid %s=%r; using %s", _ENV_TIMEOUT, raw, POLL_TIMEOUT_SEC)
        else:
            if value > 0:
                return value
            logger.warning("Ignoring non-positive %s=%r", _ENV_TIMEOUT, raw)
    return float(POLL_TIMEOUT_SEC)


def _fallback_disabled() -> bool:
    """True when ``UPSCALE_NO_FALLBACK`` asks us to fail loudly instead of using Lanczos."""
    return os.environ.get(_ENV_NO_FALLBACK, "").strip().lower() in {"1", "true", "yes", "on"}


def _fallback_or_raise(exc: BaseException, *, backend: str, elapsed: float | None = None) -> str:
    """Substitute Lanczos, or raise when the fallback is disabled.

    The learned backend failing must never be indistinguishable from a successful
    upscale: a Lanczos result has the right resolution and the audio, but zero new
    detail, so the caller cannot tell the two apart by looking at the file.
    """
    if _fallback_disabled():
        raise UpscaleError(
            f"ComfyUI {backend} upscale failed and UPSCALE_NO_FALLBACK is set: {exc}"
        ) from exc
    logger.error(
        "ComfyUI %s upscale FAILED (%s) after %.0f s; falling back to lanczos - the result "
        "has NO learned detail",
        backend,
        exc,
        elapsed if elapsed is not None else -1.0,
    )
    return "lanczos"


def _poll_comfy_history(
    comfyui_url: str,
    prompt_id: str,
    request_func: Callable[..., Any] = requests.get,
) -> dict[str, Any]:
    started = time.time()
    timeout = _poll_timeout_sec()
    while time.time() - started < timeout:
        response = request_func(
            f"{comfyui_url.rstrip('/')}/history/{prompt_id}",
            timeout=30,
        )
        if response.status_code >= 400:
            raise UpscaleError(f"ComfyUI /history failed ({response.status_code})")
        history = response.json()
        if prompt_id not in history:
            time.sleep(POLL_INTERVAL_SEC)
            continue
        entry = history[prompt_id]
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            raise UpscaleError(f"ComfyUI upscale failed: {status.get('messages')}")
        if entry.get("outputs") or status.get("status_str") == "success":
            return entry
        time.sleep(POLL_INTERVAL_SEC)
    raise UpscaleError(
        f"ComfyUI upscale timed out for prompt_id={prompt_id} "
        f"after {time.time() - started:.0f}s (limit {timeout:.0f}s)"
    )


def _run_comfy_video_workflow(
    in_path: Path,
    out_path: Path,
    workflow: dict[str, Any],
    comfyui_url: str,
    *,
    request_func_get: Callable[..., Any] = requests.get,
    request_func_post: Callable[..., Any] = requests.post,
) -> Path:
    if not _comfy_reachable(comfyui_url, request_func_get):
        raise UpscaleError(f"ComfyUI is not reachable at {comfyui_url}")

    uploaded_name = _upload_video(comfyui_url, in_path)
    workflow = copy.deepcopy(workflow)
    if "1" in workflow and workflow["1"].get("class_type") == "VHS_LoadVideo":
        workflow["1"]["inputs"]["video"] = uploaded_name

    prompt_id = _submit_prompt(comfyui_url, workflow)
    entry = _poll_comfy_history(comfyui_url, prompt_id, request_func_get)
    _download_first_video(comfyui_url, entry, out_path)
    return out_path


def upscale_video(
    in_path: Path,
    out_path: Path,
    target_width: int,
    target_height: int,
    config: UpscaleConfig,
    *,
    object_info: dict[str, Any] | None = None,
    request_func_get: Callable[..., Any] = requests.get,
    request_func_post: Callable[..., Any] = requests.post,
) -> str:
    """Upscale ``in_path`` to ``out_path`` at exact ``target_width`` x ``target_height``.

    Returns the backend name that actually ran (``seedvr2``, ``esrgan``, or ``lanczos``).
    """
    in_path = Path(in_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    requested = config.backend
    if object_info is None and requested in ("auto", "seedvr2", "esrgan"):
        object_info = fetch_object_info(config.comfyui_url, request_func_get)

    backend = select_backend(config, object_info)
    if requested != "auto" and requested != "lanczos" and backend != requested:
        if _fallback_disabled():
            raise UpscaleError(
                f"Upscale backend {requested!r} unavailable at {config.comfyui_url} "
                "and UPSCALE_NO_FALLBACK is set"
            )
        logger.error(
            "Upscale backend %r unavailable at %s; falling back to lanczos - the result "
            "has NO learned detail",
            requested,
            config.comfyui_url,
        )
        backend = "lanczos"

    if backend == "lanczos":
        lanczos_upscale(in_path, out_path, target_width, target_height)
        return "lanczos"

    if not _comfy_reachable(config.comfyui_url, request_func_get):
        if _fallback_disabled():
            raise UpscaleError(
                f"ComfyUI unreachable at {config.comfyui_url} and UPSCALE_NO_FALLBACK is set"
            )
        logger.error(
            "ComfyUI unreachable at %s; falling back to lanczos upscale - the result "
            "has NO learned detail",
            config.comfyui_url,
        )
        lanczos_upscale(in_path, out_path, target_width, target_height)
        return "lanczos"

    release_vram(config.free_vram_urls, request_func_post=request_func_post)
    free_gb = free_vram_gb(config.comfyui_url, request_func_get=request_func_get)
    if free_gb is not None and free_gb < config.min_free_vram_gb:
        if _fallback_disabled():
            raise UpscaleError(
                f"Insufficient free VRAM for {backend} upscale "
                f"({free_gb:.2f} GB free, {config.min_free_vram_gb:.2f} GB required) "
                "and UPSCALE_NO_FALLBACK is set"
            )
        logger.error(
            "Insufficient free VRAM for %s upscale: %.2f GB free, %.2f GB required; "
            "falling back to lanczos - the result has NO learned detail",
            backend,
            free_gb,
            config.min_free_vram_gb,
        )
        lanczos_upscale(in_path, out_path, target_width, target_height)
        return "lanczos"

    media = probe(in_path)
    frame_rate = media["fps"] or 24.0
    comfy_raw = out_path.with_name(f"{out_path.stem}_comfy{out_path.suffix}")

    started = time.time()
    try:
        if backend == "seedvr2":
            workflow = build_seedvr2_workflow(
                in_path.name,
                frame_rate,
                config,
            )
        else:
            workflow = build_esrgan_workflow(in_path.name, frame_rate)

        _run_comfy_video_workflow(
            in_path,
            comfy_raw,
            workflow,
            config.comfyui_url,
            request_func_get=request_func_get,
            request_func_post=request_func_post,
        )
    except (AssemblyError, UpscaleError) as exc:
        _fallback_or_raise(exc, backend=backend, elapsed=time.time() - started)
        lanczos_upscale(in_path, out_path, target_width, target_height)
        return "lanczos"

    try:
        comfy_info = probe(comfy_raw)
        if not comfy_info["has_audio"] and media["has_audio"]:
            boxed = out_path.with_name(f"{out_path.stem}_boxed{out_path.suffix}")
            apply_target_box(comfy_raw, boxed, target_width, target_height)
            mux_audio_from_source(boxed, in_path, out_path)
            boxed.unlink(missing_ok=True)
        else:
            apply_target_box(comfy_raw, out_path, target_width, target_height)
    finally:
        comfy_raw.unlink(missing_ok=True)

    return backend
