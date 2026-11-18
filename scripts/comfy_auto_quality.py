#!/usr/bin/env python3
"""Universal auto-quality processor for ComfyUI images."""

from __future__ import annotations

import argparse
import io
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

import requests
from PIL import Image, ImageFilter, ImageStat


DEFAULT_LOCAL_URL = "http://127.0.0.1:8188"
DEFAULT_WORK_PC_URL = "http://192.168.31.180:8188"
DEFAULT_LOG_PATH = Path("memory/comfy_auto_quality_log.md")
DEFAULT_UPSCALE_MODEL = "4x-UltraSharp.pth"
DEFAULT_GFPGAN_MODEL = "GFPGANv1.3.pth"
PREFERRED_CHECKPOINTS = (
    "realisticVisionV60B1_v12.safetensors",
    "juggernautXL.safetensors",
    "flux1-dev-fp8.safetensors",
)


class PipelineError(RuntimeError):
    """Pipeline-level exception with user-friendly text."""


@dataclass
class Config:
    output_dir: Path
    input_path: Optional[Path] = None
    comfy_url: Optional[str] = None
    gpu: str = "Local"
    retries: int = 4
    retry_backoff_sec: float = 1.5
    prefer_upscale: str = "auto"
    self_test: bool = False
class MarkdownLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, title: str, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(f"## {ts} | {title}\n\n{message}\n\n")
class ComfyClient:
    def __init__(self, base_url: str, retries: int, backoff: float, logger: MarkdownLogger) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_id = str(uuid.uuid4())
        self.retries = retries
        self.backoff = backoff
        self.logger = logger
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
                self.logger.log("HTTP retry", f"{method} {url} failed (attempt {attempt}/{self.retries}): {exc}")
                time.sleep(self.backoff * attempt)
        raise PipelineError(f"Failed request {method} {url}: {last_exc}") from last_exc

    def ping(self) -> None:
        self._request("GET", "/system_stats")

    def object_info(self) -> Dict[str, Any]:
        return self._request("GET", "/object_info").json()

    def upload_image_bytes(self, image_bytes: bytes, filename: str = "input.png") -> Dict[str, Any]:
        files = {"image": (filename, io.BytesIO(image_bytes), "image/png")}
        data = {"type": "input", "overwrite": "true"}
        return self._request("POST", "/upload/image", files=files, data=data).json()

    def queue_prompt(self, workflow: Dict[str, Any]) -> str:
        payload = {"prompt": workflow, "client_id": self.client_id}
        response = self._request("POST", "/prompt", json=payload).json()
        prompt_id = response.get("prompt_id")
        if not prompt_id:
            raise PipelineError(f"ComfyUI did not return prompt_id: {response}")
        return prompt_id

    def wait_result(self, prompt_id: str, timeout_sec: int = 900) -> Dict[str, Any]:
        start = time.time()
        while time.time() - start < timeout_sec:
            history = self._request("GET", f"/history/{prompt_id}").json()
            if prompt_id in history:
                return history[prompt_id]
            time.sleep(1)
        raise PipelineError(f"Timed out waiting prompt_id={prompt_id}")

    def download_image(self, image_ref: Dict[str, str]) -> bytes:
        params = {
            "filename": image_ref["filename"],
            "subfolder": image_ref.get("subfolder", ""),
            "type": image_ref.get("type", "output"),
        }
        return self._request("GET", "/view", params=params).content
class AutoQualityProcessor:
    def __init__(self, cfg: Config, logger: MarkdownLogger) -> None:
        self.cfg = cfg
        self.logger = logger
        comfy_url = self._resolve_comfy_url()
        self.client = ComfyClient(
            base_url=comfy_url, retries=cfg.retries, backoff=cfg.retry_backoff_sec, logger=logger
        )
        self.logger.log("Comfy endpoint", f"Selected endpoint: {comfy_url}")
        self.object_info = self.client.object_info()
        self.class_names = set(self.object_info.keys())

    def _resolve_comfy_url(self) -> str:
        if self.cfg.comfy_url:
            return self.cfg.comfy_url
        candidates = [DEFAULT_LOCAL_URL, DEFAULT_WORK_PC_URL]
        if self.cfg.gpu.lower() == "work-pc":
            candidates = [DEFAULT_WORK_PC_URL, DEFAULT_LOCAL_URL]
        for url in candidates:
            try:
                test_client = ComfyClient(url, self.cfg.retries, self.cfg.retry_backoff_sec, self.logger)
                test_client.ping()
                return url
            except Exception:  # noqa: BLE001
                continue
        raise PipelineError("No reachable ComfyUI endpoint (tried localhost and Work-PC).")

    def process(self, pil_image: Optional[Image.Image] = None) -> Path:
        image = pil_image or self._load_input_image()
        has_face = self._detect_face(image)
        denoise = self._auto_denoise(image)
        upscale_factor = self._choose_upscale_factor(image)
        self.logger.log(
            "Auto decisions",
            f"face_detected={has_face}, denoise={denoise:.2f}, upscale={upscale_factor}x",
        )

        current = image
        if has_face:
            current = self._try_gfpgan_restore(current)
        current = self._run_img2img_denoise(current, denoise)
        current = self._run_ultrasharp_upscale(current, upscale_factor)
        out_path = self._save_output(current)
        self.logger.log("Pipeline done", f"Saved output to {out_path.resolve()}")
        return out_path

    def _load_input_image(self) -> Image.Image:
        if not self.cfg.input_path:
            raise PipelineError("No input path provided and pil_image is None.")
        if not self.cfg.input_path.exists():
            raise PipelineError(f"Input image not found: {self.cfg.input_path}")
        return Image.open(self.cfg.input_path).convert("RGB")

    def _detect_face(self, image: Image.Image) -> bool:
        try:
            import cv2  # type: ignore

            gray = cv2.cvtColor(self._pil_to_np(image), cv2.COLOR_RGB2GRAY)
            cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(40, 40))
            return bool(len(faces))
        except Exception as exc:  # noqa: BLE001
            self.logger.log("Face detection fallback", f"OpenCV unavailable or failed, skip detection: {exc}")
            return False

    def _auto_denoise(self, image: Image.Image) -> float:
        edge = image.convert("L").filter(ImageFilter.FIND_EDGES)
        edge_stddev = ImageStat.Stat(edge).stddev[0]
        if edge_stddev < 20:
            return 0.35
        if edge_stddev < 35:
            return 0.28
        if edge_stddev < 50:
            return 0.22
        return 0.15

    def _choose_upscale_factor(self, image: Image.Image) -> int:
        if self.cfg.prefer_upscale == "2x":
            return 2
        if self.cfg.prefer_upscale == "4x":
            return 4
        width, height = image.size
        return 4 if max(width, height) < 1280 else 2

    def _node_available(self, candidates: Sequence[str]) -> Optional[str]:
        return next((name for name in candidates if name in self.class_names), None)

    def _try_gfpgan_restore(self, image: Image.Image) -> Image.Image:
        loader = self._node_available(("GFPGANLoader", "FaceRestoreModelLoader"))
        restore = self._node_available(("GFPGANRestore", "FaceRestoreWithModel"))
        if not loader or not restore:
            self.logger.log("GFPGAN skipped", "Required GFPGAN nodes are missing in object_info.")
            return image
        try:
            workflow = self._build_gfpgan_workflow(image, loader, restore)
            result = self._run_workflow_to_image(workflow, timeout=900)
            self.logger.log("GFPGAN", "Face restoration applied.")
            return result
        except Exception as exc:  # noqa: BLE001
            self.logger.log("GFPGAN degraded", f"Restore failed, continue without error: {exc}")
            return image

    def _run_img2img_denoise(self, image: Image.Image, denoise: float) -> Image.Image:
        if "CheckpointLoaderSimple" not in self.class_names:
            self.logger.log("Denoise degraded", "CheckpointLoaderSimple not found, skip denoise stage.")
            return image
        ckpt_name = self._select_checkpoint_name()
        workflow = self._build_denoise_workflow(image, denoise, ckpt_name)
        return self._run_workflow_to_image(workflow, timeout=1200)

    def _run_ultrasharp_upscale(self, image: Image.Image, factor: int) -> Image.Image:
        if "UpscaleModelLoader" not in self.class_names or "ImageUpscaleWithModel" not in self.class_names:
            self.logger.log("Upscale degraded", "UltraSharp upscale nodes not found, skip upscale stage.")
            return image
        workflow = self._build_upscale_workflow(image)
        upscaled = self._run_workflow_to_image(workflow, timeout=900)
        if factor == 2:
            return upscaled.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
        return upscaled

    def _build_gfpgan_workflow(self, image: Image.Image, loader: str, restore: str) -> Dict[str, Any]:
        upload = self.client.upload_image_bytes(self._to_png_bytes(image), filename="gfpgan_input.png")
        return {
            "1": {
                "class_type": "LoadImage",
                "inputs": {"image": upload.get("name", "gfpgan_input.png"), "upload": "image"},
            },
            "2": {"class_type": loader, "inputs": {"model_name": DEFAULT_GFPGAN_MODEL}},
            "3": {"class_type": restore, "inputs": {"image": ["1", 0], "model": ["2", 0]}},
            "4": {"class_type": "SaveImage", "inputs": {"filename_prefix": "auto_quality_face", "images": ["3", 0]}},
        }

    def _build_denoise_workflow(self, image: Image.Image, denoise: float, ckpt_name: str) -> Dict[str, Any]:
        upload = self.client.upload_image_bytes(self._to_png_bytes(image), filename="denoise_input.png")
        return {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt_name}},
            "2": {"class_type": "LoadImage", "inputs": {"image": upload.get("name", "denoise_input.png"), "upload": "image"}},
            "3": {"class_type": "VAEEncode", "inputs": {"pixels": ["2", 0], "vae": ["1", 2]}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "high quality, detailed, clean image", "clip": ["1", 1]}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "artifact, blurry, overprocessed", "clip": ["1", 1]}},
            "6": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 1337,
                    "steps": 20,
                    "cfg": 5.5,
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
            "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "auto_quality_denoise", "images": ["7", 0]}},
        }

    def _build_upscale_workflow(self, image: Image.Image) -> Dict[str, Any]:
        upload = self.client.upload_image_bytes(self._to_png_bytes(image), filename="upscale_input.png")
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": upload.get("name", "upscale_input.png"), "upload": "image"}},
            "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": DEFAULT_UPSCALE_MODEL}},
            "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
            "4": {"class_type": "SaveImage", "inputs": {"filename_prefix": "auto_quality_upscale", "images": ["3", 0]}},
        }

    def _run_workflow_to_image(self, workflow: Dict[str, Any], timeout: int) -> Image.Image:
        prompt_id = self.client.queue_prompt(workflow)
        history = self.client.wait_result(prompt_id, timeout_sec=timeout)
        ref = self._extract_first_image_ref(history)
        if not ref:
            raise PipelineError(f"No output image found in history: {history}")
        content = self.client.download_image(ref)
        return Image.open(io.BytesIO(content)).convert("RGB")

    def _extract_first_image_ref(self, history_item: Dict[str, Any]) -> Optional[Dict[str, str]]:
        outputs = history_item.get("outputs", {})
        for value in outputs.values():
            images = value.get("images", [])
            if images:
                return images[0]
        return None

    def _select_checkpoint_name(self) -> str:
        info = self.object_info.get("CheckpointLoaderSimple", {})
        inputs = info.get("input", {}) if isinstance(info, dict) else {}
        required = inputs.get("required", {}) if isinstance(inputs, dict) else {}
        ckpt_values = required.get("ckpt_name")
        available: Iterable[str] = ()
        if isinstance(ckpt_values, (list, tuple)) and ckpt_values and isinstance(ckpt_values[0], list):
            available = tuple(ckpt_values[0])
        for preferred in PREFERRED_CHECKPOINTS:
            if preferred in available:
                return preferred
        return next(iter(available), "realisticVisionV60B1_v12.safetensors")

    def _save_output(self, image: Image.Image) -> Path:
        self.cfg.output_dir.mkdir(parents=True, exist_ok=True)
        stem = self.cfg.input_path.stem if self.cfg.input_path else "pil_image"
        out_path = self.cfg.output_dir / f"{stem}_auto_quality.png"
        image.save(out_path, format="PNG")
        return out_path

    def _to_png_bytes(self, image: Image.Image) -> bytes:
        with io.BytesIO() as buf:
            image.save(buf, format="PNG")
            return buf.getvalue()

    def _pil_to_np(self, image: Image.Image) -> Any:
        import numpy as np  # type: ignore

        return np.array(image.convert("RGB"))
def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="ComfyUI auto-quality processor (face restore + denoise + upscale).")
    parser.add_argument("--input", dest="input_path", help="Input image path.")
    parser.add_argument("--output", dest="output_dir", default="output", help="Output directory.")
    parser.add_argument("--gpu", default="Local", choices=["Local", "Work-PC"], help="Preferred Comfy host.")
    parser.add_argument("--comfy-url", dest="comfy_url", default=None, help="Override ComfyUI API URL.")
    parser.add_argument("--retries", type=int, default=4, help="HTTP retry attempts.")
    parser.add_argument("--retry-backoff-sec", type=float, default=1.5, help="Retry backoff multiplier.")
    parser.add_argument("--upscale", choices=["auto", "2x", "4x"], default="auto", help="Preferred upscale factor.")
    parser.add_argument("--self-test", action="store_true", help="Run API/object_info checks and local quality analysis.")
    args = parser.parse_args()
    return Config(
        input_path=Path(args.input_path) if args.input_path else None,
        output_dir=Path(args.output_dir),
        comfy_url=args.comfy_url,
        gpu=args.gpu,
        retries=args.retries,
        retry_backoff_sec=args.retry_backoff_sec,
        prefer_upscale=args.upscale,
        self_test=args.self_test,
    )
def run_self_test(cfg: Config, logger: MarkdownLogger) -> int:
    processor = AutoQualityProcessor(cfg, logger)
    sample = Image.new("RGB", (256, 256), color=(120, 120, 120))
    denoise = processor._auto_denoise(sample)
    upscale = processor._choose_upscale_factor(sample)
    logger.log("Self-test", f"object_info_classes={len(processor.class_names)}, denoise={denoise:.2f}, upscale={upscale}x")
    print("[OK] Self-test passed: API reachable, object_info loaded, heuristics operational.")
    return 0
def process_pil_image(pil_image: Image.Image, output_dir: str = "output", gpu: str = "Local") -> Path:
    """
    Programmatic API entry point for PIL inputs.

    Example:
        from PIL import Image
        from scripts.comfy_auto_quality import process_pil_image
        result = process_pil_image(Image.open("photo.jpg"), output_dir="out", gpu="Work-PC")
    """
    cfg = Config(output_dir=Path(output_dir), gpu=gpu)
    logger = MarkdownLogger(DEFAULT_LOG_PATH)
    processor = AutoQualityProcessor(cfg, logger)
    return processor.process(pil_image=pil_image)
def main() -> int:
    cfg = parse_args()
    logger = MarkdownLogger(DEFAULT_LOG_PATH)
    try:
        if cfg.self_test:
            return run_self_test(cfg, logger)
        if not cfg.input_path:
            raise PipelineError("CLI mode requires --input (or use --self-test).")
        processor = AutoQualityProcessor(cfg, logger)
        output = processor.process()
        print(f"[OK] Saved: {output}")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.log("Pipeline error", f"{type(exc).__name__}: {exc}")
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
