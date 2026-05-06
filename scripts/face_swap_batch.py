#!/usr/bin/env python3
"""
Batch face swap processor for model photography.

Features:
- Batch processing of JPG/PNG files
- InsightFace detection (buffalo_l)
- InSwapper ONNX model execution on CPU
- Optional GFPGAN enhancement after swap
- Markdown logging to memory/faceswap_log.md
- YAML config loading
- Synthetic self-test that works without heavy model files

Dependencies:
    pip install insightface onnxruntime pillow numpy pyyaml
Optional for enhancement:
    pip install gfpgan opencv-python
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


DEFAULT_CONFIG_PATH = Path("face_swap_batch.yaml")
DEFAULT_LOG_PATH = Path("memory/faceswap_log.md")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


class FaceSwapError(RuntimeError):
    """Pipeline-level error."""


class MarkdownLogger:
    """Simple markdown logger that appends sections."""

    def __init__(self, path: Path = DEFAULT_LOG_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, title: str, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with self.path.open("a", encoding="utf-8") as f:
            f.write(f"## {ts} | {title}\n\n{message}\n\n")


@dataclass
class FaceSwapConfig:
    inswapper_model: Path
    insightface_model_root: Path
    gfpgan_model: Optional[Path]
    target_dir: Path
    source_face: Path
    output_dir: Path
    enhance: bool = True
    det_size: Tuple[int, int] = (640, 640)
    providers: Tuple[str, ...] = ("CPUExecutionProvider",)


_RUNTIME: Dict[str, Any] = {
    "config": None,
    "logger": MarkdownLogger(),
    "face_analyzer": None,
    "face_swapper": None,
    "source_face": None,
    "mock_mode": False,
}


def _load_yaml_config(path: Path) -> Dict[str, Any]:
    if yaml is None:
        raise FaceSwapError("PyYAML is required for config support: pip install pyyaml")
    if not path.exists():
        raise FaceSwapError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise FaceSwapError("Config root must be a mapping.")
    return data


def _build_config(raw: Dict[str, Any]) -> FaceSwapConfig:
    models = raw.get("models", {})
    paths = raw.get("paths", {})
    processing = raw.get("processing", {})
    return FaceSwapConfig(
        inswapper_model=Path(models.get("inswapper_model", "")).expanduser(),
        insightface_model_root=Path(models.get("insightface_model_root", "")).expanduser(),
        gfpgan_model=Path(models["gfpgan_model"]).expanduser() if models.get("gfpgan_model") else None,
        target_dir=Path(paths.get("target_dir", "target_images")).expanduser(),
        source_face=Path(paths.get("source_face", "source_face.jpg")).expanduser(),
        output_dir=Path(paths.get("output_dir", "output")).expanduser(),
        enhance=bool(processing.get("enhance", True)),
        det_size=tuple(processing.get("det_size", [640, 640])),  # type: ignore[arg-type]
        providers=tuple(processing.get("providers", ["CPUExecutionProvider"])),
    )


def _init_runtime(config: FaceSwapConfig, logger: Optional[MarkdownLogger] = None, mock_mode: bool = False) -> None:
    if logger is not None:
        _RUNTIME["logger"] = logger
    _RUNTIME["config"] = config
    _RUNTIME["mock_mode"] = mock_mode

    if mock_mode:
        _RUNTIME["face_analyzer"] = "mock"
        _RUNTIME["face_swapper"] = "mock"
        _RUNTIME["source_face"] = {"bbox": np.array([0, 0, 64, 64]), "kps": np.zeros((5, 2))}
        return

    try:
        import insightface
    except ImportError as exc:  # pragma: no cover
        raise FaceSwapError("insightface is required: pip install insightface") from exc

    analyzer = insightface.app.FaceAnalysis(
        name="buffalo_l",
        root=str(config.insightface_model_root),
        providers=list(config.providers),
    )
    analyzer.prepare(ctx_id=-1, det_size=config.det_size)

    swapper = insightface.model_zoo.get_model(
        str(config.inswapper_model),
        providers=list(config.providers),
    )

    source_img = _read_image_rgb(config.source_face)
    source_faces = analyzer.get(_rgb_to_bgr(source_img))
    if not source_faces:
        raise FaceSwapError(f"No face found in source image: {config.source_face}")

    _RUNTIME["face_analyzer"] = analyzer
    _RUNTIME["face_swapper"] = swapper
    _RUNTIME["source_face"] = source_faces[0]
    _RUNTIME["logger"].log(
        "Runtime init",
        f"CPU providers={config.providers}, source_face={config.source_face}, det_size={config.det_size}",
    )


def _read_image_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as img:
        return np.array(img.convert("RGB"), dtype=np.uint8)


def _save_image_rgb(image: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(image, 0, 255).astype(np.uint8)).save(path)


def _rgb_to_bgr(image: np.ndarray) -> np.ndarray:
    return image[:, :, ::-1]


def _bgr_to_rgb(image: np.ndarray) -> np.ndarray:
    return image[:, :, ::-1]


def detect_faces(image_path: str | Path) -> List[Tuple[int, int, int, int]]:
    """Detect face rectangles in an image."""
    path = Path(image_path)
    analyzer = _RUNTIME.get("face_analyzer")
    if analyzer is None:
        raise FaceSwapError("Runtime is not initialized. Call _init_runtime first.")

    image_rgb = _read_image_rgb(path)
    if _RUNTIME["mock_mode"]:
        h, w = image_rgb.shape[:2]
        return [(w // 4, h // 4, (w * 3) // 4, (h * 3) // 4)]

    faces = analyzer.get(_rgb_to_bgr(image_rgb))
    rects: List[Tuple[int, int, int, int]] = []
    for face in faces:
        x1, y1, x2, y2 = [int(v) for v in face.bbox.tolist()]
        rects.append((x1, y1, x2, y2))
    return rects


def apply_inswapper(image: np.ndarray, face_rect: Sequence[int], model_path: str | Path) -> np.ndarray:
    """
    Apply face swap on one rectangle.
    model_path is accepted for API compatibility, runtime uses initialized swapper.
    """
    _ = model_path
    x1, y1, x2, y2 = [int(v) for v in face_rect]
    if _RUNTIME["mock_mode"]:
        src = image[max(0, y1 - 5) : max(0, y1 - 5) + 20, max(0, x1 - 5) : max(0, x1 - 5) + 20]
        out = image.copy()
        if src.size:
            patch = np.tile(np.array([235, 125, 125], dtype=np.uint8), (max(1, y2 - y1), max(1, x2 - x1), 1))
            out[y1:y2, x1:x2] = patch
        return out

    analyzer = _RUNTIME["face_analyzer"]
    swapper = _RUNTIME["face_swapper"]
    source_face = _RUNTIME["source_face"]

    img_bgr = _rgb_to_bgr(image)
    candidates = analyzer.get(img_bgr)
    target_face = None
    for face in candidates:
        bbox = [int(v) for v in face.bbox.tolist()]
        if abs(bbox[0] - x1) <= 5 and abs(bbox[1] - y1) <= 5 and abs(bbox[2] - x2) <= 5 and abs(bbox[3] - y2) <= 5:
            target_face = face
            break
    if target_face is None:
        return image

    swapped_bgr = swapper.get(img_bgr, target_face, source_face, paste_back=True)
    return _bgr_to_rgb(swapped_bgr)


def apply_gfpgan(image: np.ndarray, model_path: str | Path) -> np.ndarray:
    """Enhance swapped image using GFPGAN when available."""
    if _RUNTIME["mock_mode"]:
        arr = image.astype(np.int16)
        arr = np.clip((arr - 128) * 1.05 + 128, 0, 255)
        return arr.astype(np.uint8)

    try:
        import cv2
        from gfpgan import GFPGANer
    except ImportError:
        _RUNTIME["logger"].log("GFPGAN skipped", "gfpgan/opencv not installed, returning original image.")
        return image

    bgr = _rgb_to_bgr(image)
    enhancer = GFPGANer(
        model_path=str(model_path),
        upscale=1,
        arch="clean",
        channel_multiplier=2,
        bg_upsampler=None,
    )
    _, _, restored = enhancer.enhance(bgr, has_aligned=False, only_center_face=False, paste_back=True)
    if restored is None:
        return image
    return _bgr_to_rgb(restored)


def single_swap(target_path: str | Path, source_face_path: str | Path, enhance: bool = True) -> Optional[np.ndarray]:
    """Swap all faces in one target image."""
    cfg: FaceSwapConfig = _RUNTIME["config"]
    _ = source_face_path
    target = Path(target_path)
    rects = detect_faces(target)
    if not rects:
        _RUNTIME["logger"].log("Warning", f"No faces found, skipped: {target}")
        return None

    image = _read_image_rgb(target)
    result = image.copy()
    for rect in rects:
        result = apply_inswapper(result, rect, cfg.inswapper_model)

    if enhance and cfg.gfpgan_model:
        result = apply_gfpgan(result, cfg.gfpgan_model)
    return result


def batch_swap(target_dir: str | Path, source_face: str | Path, output_dir: str | Path, enhance: bool = True) -> Dict[str, int]:
    """Batch face swap over all jpg/png images in target_dir."""
    src = Path(source_face)
    in_dir = Path(target_dir)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted([p for p in in_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS])
    stats = {"processed": 0, "swapped": 0, "skipped": 0, "failed": 0}

    for path in files:
        stats["processed"] += 1
        try:
            result = single_swap(path, src, enhance=enhance)
            if result is None:
                stats["skipped"] += 1
                continue
            out_path = out_dir / f"swapped_{path.name}"
            _save_image_rgb(result, out_path)
            stats["swapped"] += 1
        except Exception as exc:  # noqa: BLE001
            stats["failed"] += 1
            _RUNTIME["logger"].log("Error", f"Failed file={path}: {exc}")
    _RUNTIME["logger"].log("Batch summary", str(stats))
    return stats


def _write_default_config(path: Path) -> None:
    payload = {
        "models": {
            "inswapper_model": r"C:\Users\user\comfyui\ComfyUI_windows_portable\ComfyUI\models\reactor\faces\inswapper_128.onnx",
            "insightface_model_root": r"C:\Users\user\comfyui\ComfyUI_windows_portable\ComfyUI\models\insightface\buffalo_l",
            "gfpgan_model": r"C:\Users\user\comfyui\ComfyUI_windows_portable\ComfyUI\models\facerestore_models\GFPGANv1.3.pth",
        },
        "paths": {
            "target_dir": "target_images",
            "source_face": "source_face.jpg",
            "output_dir": "output_swapped",
        },
        "processing": {
            "enhance": True,
            "det_size": [640, 640],
            "providers": ["CPUExecutionProvider"],
        },
    }
    if yaml is None:
        raise FaceSwapError("PyYAML is required to write config.")
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")


def _run_self_test() -> None:
    logger = MarkdownLogger()
    base = Path("tmp_faceswap_selftest")
    target_dir = base / "targets"
    output_dir = base / "output"
    source_path = base / "source_face.jpg"
    target_dir.mkdir(parents=True, exist_ok=True)

    source = Image.new("RGB", (128, 128), (40, 40, 40))
    draw = ImageDraw.Draw(source)
    draw.ellipse((24, 20, 104, 110), fill=(230, 200, 160))
    source.save(source_path)

    for i in range(3):
        img = Image.new("RGB", (256, 256), (25, 25, 25))
        d = ImageDraw.Draw(img)
        x = 64 + i * 12
        d.ellipse((x, 64, x + 80, 164), fill=(190, 180, 170))
        img.save(target_dir / f"target_{i + 1}.png")

    cfg = FaceSwapConfig(
        inswapper_model=Path("mock.onnx"),
        insightface_model_root=Path("mock_models"),
        gfpgan_model=Path("mock_gfpgan.pth"),
        target_dir=target_dir,
        source_face=source_path,
        output_dir=output_dir,
        enhance=True,
    )
    _init_runtime(cfg, logger=logger, mock_mode=True)
    stats = batch_swap(cfg.target_dir, cfg.source_face, cfg.output_dir, enhance=True)
    produced = sorted(output_dir.glob("swapped_*"))
    if stats["swapped"] != 3 or len(produced) != 3:
        raise FaceSwapError(f"Self-test failed: stats={stats}, files={len(produced)}")
    logger.log("Self-test", f"OK | stats={stats}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch face swap processor")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to face_swap_batch.yaml")
    parser.add_argument("--init-config", action="store_true", help="Write default YAML config and exit")
    parser.add_argument("--self-test", action="store_true", help="Run synthetic self-test and exit")
    args = parser.parse_args()

    if args.init_config:
        _write_default_config(args.config)
        print(f"Default config written to: {args.config}")
        return

    if args.self_test:
        _run_self_test()
        print("Self-test passed")
        return

    raw = _load_yaml_config(args.config)
    cfg = _build_config(raw)
    logger = MarkdownLogger()
    _init_runtime(cfg, logger=logger, mock_mode=False)
    stats = batch_swap(cfg.target_dir, cfg.source_face, cfg.output_dir, enhance=cfg.enhance)
    print(stats)


if __name__ == "__main__":
    main()
