#!/usr/bin/env python3
"""
Neural face-swap pipeline for fashion / editorial photography.

Pipeline (deterministic core):
  1) Load source identity from ``--source`` (face reference image).
  2) Detect faces on ``--target`` (body / scene image) with InsightFace ``buffalo_l``.
  3) Swap using ``inswapper_128.onnx`` (ONNXRuntime) with ``paste_back=True``.
  4) Write ``--output``.

Optional ComfyUI stage (recommended for lighting blend and print-ready polish):
  - Export an **API-format** workflow from ComfyUI (Save API Format) that loads your
    Flux / GGUF stack (for example flux2-klein GGUF under ``models/unet`` with
    ComfyUI-GGUF). Set the ``LoadImage`` node filename to the placeholder
    ``__FACE_SWAP_INPUT__`` before saving, or pass ``--comfy-workflow`` pointing to
    a JSON that still contains that placeholder in the LoadImage ``image`` field.
  - The script uploads the swapped PNG, substitutes the placeholder with Comfy's
    returned filename, queues ``POST /prompt``, downloads the first ``SaveImage`` output.

Dependencies:
    pip install insightface onnxruntime pillow numpy opencv-python-headless requests

Optional (ComfyUI HTTP client):
    pip install requests

Model files (user-provided):
    - ``inswapper_128.onnx`` (e.g. from ReActor / InsightFace model zoo)
    - InsightFace ``buffalo_l`` pack under ``--insightface-root`` (parent of ``buffalo_l/``)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import requests
from PIL import Image


INPUT_PLACEHOLDER = "__FACE_SWAP_INPUT__"


class FaceSwapNeuralError(RuntimeError):
    """User-facing pipeline error."""


@dataclass
class NeuralSwapConfig:
    source_path: Path
    target_path: Path
    output_path: Path
    inswapper_model: Path
    insightface_root: Path
    det_size: Tuple[int, int] = (640, 640)
    providers: Tuple[str, ...] = ("CUDAExecutionProvider", "CPUExecutionProvider")
    face_index: int = 0
    swap_all_faces: bool = False
    comfy_url: Optional[str] = None
    comfy_workflow: Optional[Path] = None
    comfy_checkpoint_refine: bool = False
    comfy_denoise: float = 0.22
    comfy_steps: int = 18
    comfy_cfg: float = 5.5
    comfy_positive: str = (
        "editorial fashion photograph, natural skin texture, soft diffused studio light, "
        "high-end magazine quality, coherent shadows, sharp fabric detail"
    )
    comfy_negative: str = "blur, low quality, distorted face, double face, plastic skin, watermark, text, logo"
    comfy_timeout_sec: int = 1200
    comfy_retries: int = 4
    comfy_retry_backoff: float = 1.5
    seed: int = 424242


def _rgb_to_bgr(image: np.ndarray) -> np.ndarray:
    return image[:, :, ::-1]


def _bgr_to_rgb(image: np.ndarray) -> np.ndarray:
    return image[:, :, ::-1]


def _read_image_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as img:
        return np.array(img.convert("RGB"), dtype=np.uint8)


def _save_image_rgb(image: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(image, 0, 255).astype(np.uint8)).save(path)


def _face_area(face: Any) -> float:
    x1, y1, x2, y2 = face.bbox.tolist()
    return float(max(0.0, x2 - x1) * max(0.0, y2 - y1))


def _pick_providers(requested: Sequence[str]) -> List[str]:
    try:
        import onnxruntime as ort  # type: ignore

        available = set(ort.get_available_providers())
    except Exception:  # noqa: BLE001
        return list(requested)
    ordered: List[str] = []
    for p in requested:
        if p in available and p not in ordered:
            ordered.append(p)
    if not ordered:
        return ["CPUExecutionProvider"]
    return ordered


def _default_inswapper_path() -> Path:
    env = os.environ.get("INSWAPPER_ONNX")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".insightface" / "models" / "inswapper_128.onnx"


def _default_insightface_root() -> Path:
    return Path.home() / ".insightface" / "models"


class ComfyHttpClient:
    """Minimal ComfyUI HTTP API client (aligned with scripts/comfy_auto_quality.py)."""

    def __init__(self, base_url: str, retries: int, backoff: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_id = str(uuid.uuid4())
        self.retries = max(1, retries)
        self.backoff = max(0.1, backoff)
        self.session = requests.Session()

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.request(method, url, timeout=120, **kwargs)
                response.raise_for_status()
                return response
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                time.sleep(self.backoff * attempt)
        raise FaceSwapNeuralError(f"HTTP {method} {url} failed after retries: {last_exc}") from last_exc

    def ping(self) -> None:
        self._request("GET", "/system_stats")

    def object_info(self) -> Dict[str, Any]:
        return self._request("GET", "/object_info").json()

    def upload_png(self, image: Image.Image, filename: str = "face_swap_neural_input.png") -> Dict[str, Any]:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)
        files = {"image": (filename, buf, "image/png")}
        data = {"type": "input", "overwrite": "true"}
        return self._request("POST", "/upload/image", files=files, data=data).json()

    def queue_prompt(self, workflow: Dict[str, Any]) -> str:
        payload = {"prompt": workflow, "client_id": self.client_id}
        data = self._request("POST", "/prompt", json=payload).json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise FaceSwapNeuralError(f"ComfyUI did not return prompt_id: {data}")
        return str(prompt_id)

    def wait_result(self, prompt_id: str, timeout_sec: int) -> Dict[str, Any]:
        start = time.time()
        while time.time() - start < timeout_sec:
            history = self._request("GET", f"/history/{prompt_id}").json()
            if prompt_id in history:
                return history[prompt_id]
            time.sleep(1.0)
        raise FaceSwapNeuralError(f"Timed out waiting for prompt_id={prompt_id}")

    def download_image(self, image_ref: Dict[str, str]) -> Image.Image:
        params = {
            "filename": image_ref["filename"],
            "subfolder": image_ref.get("subfolder", ""),
            "type": image_ref.get("type", "output"),
        }
        content = self._request("GET", "/view", params=params).content
        return Image.open(io.BytesIO(content)).convert("RGB")


def _extract_first_output_image(history_item: Dict[str, Any]) -> Optional[Dict[str, str]]:
    outputs = history_item.get("outputs", {})
    for block in outputs.values():
        images = block.get("images", [])
        if images:
            return images[0]
    return None


def _substitute_placeholder(obj: Any, replacement: str) -> Any:
    if isinstance(obj, dict):
        return {k: _substitute_placeholder(v, replacement) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_placeholder(v, replacement) for v in obj]
    if isinstance(obj, str) and obj == INPUT_PLACEHOLDER:
        return replacement
    return obj


def _load_workflow_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise FaceSwapNeuralError("Workflow JSON root must be an object (API prompt format).")
    return data


def _patch_first_loadimage(workflow: Dict[str, Any], image_name: str) -> Dict[str, Any]:
    """If no placeholder is present, set the first LoadImage node's image input."""
    out = json.loads(json.dumps(workflow))
    for node in out.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") == "LoadImage":
            inputs = node.setdefault("inputs", {})
            inputs["image"] = image_name
            return out
    raise FaceSwapNeuralError("Workflow has no LoadImage node to patch.")


def _workflow_contains_placeholder(workflow: Dict[str, Any]) -> bool:
    blob = json.dumps(workflow)
    return INPUT_PLACEHOLDER in blob


def _run_comfy_workflow(client: ComfyHttpClient, workflow: Dict[str, Any], timeout_sec: int) -> Image.Image:
    prompt_id = client.queue_prompt(workflow)
    history = client.wait_result(prompt_id, timeout_sec=timeout_sec)
    ref = _extract_first_output_image(history)
    if not ref:
        raise FaceSwapNeuralError(f"No output image in ComfyUI history for prompt_id={prompt_id}")
    return client.download_image(ref)


def _select_checkpoint_name(object_info: Dict[str, Any]) -> str:
    info = object_info.get("CheckpointLoaderSimple", {})
    inputs = info.get("input", {}) if isinstance(info, dict) else {}
    required = inputs.get("required", {}) if isinstance(inputs, dict) else {}
    ckpt_values = required.get("ckpt_name")
    available: Tuple[str, ...] = ()
    if isinstance(ckpt_values, (list, tuple)) and ckpt_values and isinstance(ckpt_values[0], (list, tuple)):
        available = tuple(str(x) for x in ckpt_values[0])
    preferred = (
        "flux2-klein-Q4_0.gguf",
        "flux2-klein-Q8_0.gguf",
        "flux1-dev-fp8.safetensors",
        "flux1-schnell-fp8.safetensors",
        "juggernautXL.safetensors",
        "realisticVisionV60B1_v12.safetensors",
    )
    lower_index = {n.lower(): n for n in available}
    for p in preferred:
        key = p.lower()
        if key in lower_index:
            return lower_index[key]
    for n in available:
        if "flux" in n.lower():
            return n
    if available:
        return available[0]
    raise FaceSwapNeuralError("No checkpoints reported by ComfyUI CheckpointLoaderSimple.")


def _build_checkpoint_img2img_workflow(
    *,
    upload_name: str,
    ckpt_name: str,
    denoise: float,
    steps: int,
    cfg: float,
    seed: int,
    positive: str,
    negative: str,
) -> Dict[str, Any]:
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt_name}},
        "2": {"class_type": "LoadImage", "inputs": {"image": upload_name, "upload": "image"}},
        "3": {"class_type": "VAEEncode", "inputs": {"pixels": ["2", 0], "vae": ["1", 2]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["1", 1]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": denoise,
                "model": ["1", 0],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["3", 0],
            },
        },
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "face_swap_neural_refine", "images": ["7", 0]}},
    }


def run_inswapper_swap(cfg: NeuralSwapConfig) -> np.ndarray:
    """Run buffalo_l detection + inswapper; return RGB uint8 array."""
    try:
        import insightface  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise FaceSwapNeuralError("Install insightface: pip install insightface") from exc

    if not cfg.inswapper_model.is_file():
        raise FaceSwapNeuralError(f"inswapper model not found: {cfg.inswapper_model}")
    if not cfg.source_path.is_file():
        raise FaceSwapNeuralError(f"Source image not found: {cfg.source_path}")
    if not cfg.target_path.is_file():
        raise FaceSwapNeuralError(f"Target image not found: {cfg.target_path}")

    providers = _pick_providers(cfg.providers)
    root = str(cfg.insightface_root.expanduser())
    analyzer = insightface.app.FaceAnalysis(name="buffalo_l", root=root, providers=providers)
    analyzer.prepare(ctx_id=0 if "CUDAExecutionProvider" in providers else -1, det_size=cfg.det_size)

    swapper = insightface.model_zoo.get_model(str(cfg.inswapper_model.expanduser()), providers=providers)

    source_rgb = _read_image_rgb(cfg.source_path)
    source_faces = analyzer.get(_rgb_to_bgr(source_rgb))
    if not source_faces:
        raise FaceSwapNeuralError(f"No face detected in source image: {cfg.source_path}")
    source_face = max(source_faces, key=_face_area)

    target_rgb = _read_image_rgb(cfg.target_path)
    target_bgr = _rgb_to_bgr(target_rgb)
    target_faces = analyzer.get(target_bgr)
    if not target_faces:
        raise FaceSwapNeuralError(f"No face detected in target image: {cfg.target_path}")

    ordered = sorted(target_faces, key=_face_area, reverse=True)
    if cfg.swap_all_faces:
        out_bgr = target_bgr
        for face in ordered:
            out_bgr = swapper.get(out_bgr, face, source_face, paste_back=True)
        return _bgr_to_rgb(out_bgr)

    if cfg.face_index < 0 or cfg.face_index >= len(ordered):
        raise FaceSwapNeuralError(
            f"face_index={cfg.face_index} out of range (0..{len(ordered) - 1} for {len(ordered)} detected faces)."
        )
    target_face = ordered[cfg.face_index]
    swapped_bgr = swapper.get(target_bgr, target_face, source_face, paste_back=True)
    return _bgr_to_rgb(swapped_bgr)


def run_comfy_refinement(cfg: NeuralSwapConfig, swapped_rgb: np.ndarray) -> np.ndarray:
    if not cfg.comfy_url:
        raise FaceSwapNeuralError("comfy_url is required for ComfyUI refinement.")
    client = ComfyHttpClient(cfg.comfy_url, cfg.comfy_retries, cfg.comfy_retry_backoff)
    client.ping()

    pil_in = Image.fromarray(swapped_rgb)
    upload = client.upload_png(pil_in, filename=f"face_swap_neural_{uuid.uuid4().hex[:10]}.png")
    upload_name = upload.get("name")
    if not upload_name:
        raise FaceSwapNeuralError(f"Unexpected upload response: {upload}")

    if cfg.comfy_workflow and cfg.comfy_workflow.is_file():
        wf = _load_workflow_json(cfg.comfy_workflow)
        if _workflow_contains_placeholder(wf):
            wf = _substitute_placeholder(wf, upload_name)
        else:
            wf = _patch_first_loadimage(wf, upload_name)
        refined = _run_comfy_workflow(client, wf, cfg.comfy_timeout_sec)
        return np.array(refined, dtype=np.uint8)

    if cfg.comfy_checkpoint_refine:
        object_info = client.object_info()
        if "CheckpointLoaderSimple" not in object_info:
            raise FaceSwapNeuralError("ComfyUI has no CheckpointLoaderSimple; export a GGUF Flux workflow instead.")
        ckpt = _select_checkpoint_name(object_info)
        wf = _build_checkpoint_img2img_workflow(
            upload_name=upload_name,
            ckpt_name=ckpt,
            denoise=cfg.comfy_denoise,
            steps=cfg.comfy_steps,
            cfg=cfg.comfy_cfg,
            seed=cfg.seed,
            positive=cfg.comfy_positive,
            negative=cfg.comfy_negative,
        )
        refined = _run_comfy_workflow(client, wf, cfg.comfy_timeout_sec)
        return np.array(refined, dtype=np.uint8)

    raise FaceSwapNeuralError(
        "ComfyUI URL set but neither --comfy-workflow nor --comfy-checkpoint-refine was specified. "
        "For flux2-klein GGUF, export an API workflow and set its LoadImage to "
        f"{INPUT_PLACEHOLDER!r}, then pass --comfy-workflow path/to/workflow.json"
    )


def run_pipeline(cfg: NeuralSwapConfig) -> Path:
    swapped = run_inswapper_swap(cfg)
    if cfg.comfy_url:
        swapped = run_comfy_refinement(cfg, swapped)
    _save_image_rgb(swapped, cfg.output_path)
    return cfg.output_path


def _parse_providers(spec: str) -> Tuple[str, ...]:
    parts = tuple(p.strip() for p in spec.split(",") if p.strip())
    return parts or ("CUDAExecutionProvider", "CPUExecutionProvider")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Neural face swap (buffalo_l + inswapper_128) with optional ComfyUI refinement."
    )
    p.add_argument("--source", type=Path, required=True, help="Source face / identity image.")
    p.add_argument("--target", type=Path, required=True, help="Target body / fashion scene image.")
    p.add_argument("--output", type=Path, required=True, help="Output image path (PNG or JPEG).")
    p.add_argument("--inswapper", type=Path, default=None, help=f"Path to inswapper_128.onnx (default: {_default_inswapper_path()}).")
    p.add_argument(
        "--insightface-root",
        type=Path,
        default=None,
        help=f"InsightFace models root, parent of buffalo_l/ (default: {_default_insightface_root()}).",
    )
    p.add_argument("--det-size", type=int, nargs=2, default=[640, 640], metavar=("W", "H"), help="Detector size for buffalo_l.")
    p.add_argument(
        "--providers",
        type=str,
        default="CUDAExecutionProvider,CPUExecutionProvider",
        help="ONNXRuntime provider order, comma-separated.",
    )
    p.add_argument("--cpu-only", action="store_true", help="Use CPUExecutionProvider only.")
    p.add_argument(
        "--face-index",
        type=int,
        default=0,
        help="Which target face to swap when multiple are detected (0 = largest).",
    )
    p.add_argument("--swap-all-faces", action="store_true", help="Swap every detected face on the target.")
    p.add_argument("--comfy-url", type=str, default=None, help="ComfyUI base URL, e.g. http://127.0.0.1:8188")
    p.add_argument(
        "--comfy-workflow",
        type=Path,
        default=None,
        help="API-format JSON workflow; use LoadImage filename placeholder __FACE_SWAP_INPUT__ or first LoadImage is patched.",
    )
    p.add_argument(
        "--comfy-checkpoint-refine",
        action="store_true",
        help="If no --comfy-workflow: run low-denoise img2img via CheckpointLoaderSimple (SD/XL/Flux safetensors ckpt, not GGUF).",
    )
    p.add_argument("--comfy-denoise", type=float, default=0.22, help="Denoise strength for checkpoint img2img refine.")
    p.add_argument("--comfy-steps", type=int, default=18, help="Sampler steps for checkpoint refine.")
    p.add_argument("--comfy-cfg", type=float, default=5.5, help="CFG for checkpoint refine.")
    p.add_argument("--comfy-timeout", type=int, default=1200, help="ComfyUI prompt timeout seconds.")
    p.add_argument("--comfy-retries", type=int, default=4, help="HTTP retries for ComfyUI.")
    p.add_argument("--seed", type=int, default=424242, help="RNG seed for checkpoint KSampler.")
    p.add_argument(
        "--comfy-positive",
        type=str,
        default=None,
        help="Override positive prompt for checkpoint refine.",
    )
    p.add_argument(
        "--comfy-negative",
        type=str,
        default=None,
        help="Override negative prompt for checkpoint refine.",
    )
    return p


def main() -> int:
    args = build_arg_parser().parse_args()
    providers: Tuple[str, ...]
    if args.cpu_only:
        providers = ("CPUExecutionProvider",)
    else:
        providers = _parse_providers(args.providers)

    cfg = NeuralSwapConfig(
        source_path=args.source.expanduser(),
        target_path=args.target.expanduser(),
        output_path=args.output.expanduser(),
        inswapper_model=(args.inswapper or _default_inswapper_path()).expanduser(),
        insightface_root=(args.insightface_root or _default_insightface_root()).expanduser(),
        det_size=(int(args.det_size[0]), int(args.det_size[1])),
        providers=providers,
        face_index=args.face_index,
        swap_all_faces=args.swap_all_faces,
        comfy_url=args.comfy_url,
        comfy_workflow=args.comfy_workflow.expanduser() if args.comfy_workflow else None,
        comfy_checkpoint_refine=bool(args.comfy_checkpoint_refine),
        comfy_denoise=float(args.comfy_denoise),
        comfy_steps=int(args.comfy_steps),
        comfy_cfg=float(args.comfy_cfg),
        comfy_positive=args.comfy_positive or NeuralSwapConfig.comfy_positive,
        comfy_negative=args.comfy_negative or NeuralSwapConfig.comfy_negative,
        comfy_timeout_sec=int(args.comfy_timeout),
        comfy_retries=int(args.comfy_retries),
        seed=int(args.seed),
    )

    try:
        out = run_pipeline(cfg)
        print(f"[OK] Wrote {out.resolve()}")
        return 0
    except FaceSwapNeuralError as exc:
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
