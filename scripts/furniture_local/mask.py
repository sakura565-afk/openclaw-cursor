"""Построение маски зоны ткани/обивки для inpaint."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageDraw, ImageFilter

try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False


@dataclass(frozen=True)
class MaskBuildResult:
    """Путь к сохранённой маске и метаданные для отчёта."""

    path: Path
    bbox: tuple[int, int, int, int]
    area: int
    source: Literal["file", "bbox", "color"]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_rgb(image_path: Path) -> tuple[Image.Image, tuple[int, int]]:
    with Image.open(image_path) as im:
        rgb = im.convert("RGB")
        return rgb.copy(), rgb.size


def _mask_bbox(mask_l: Image.Image) -> tuple[int, int, int, int]:
    """Ограничивающий прямоугольник по пикселям с alpha/яркостью > 127."""
    if _HAS_NUMPY:
        arr = np.array(mask_l)
        ys, xs = np.where(arr > 127)
        if xs.size == 0:
            w, h = mask_l.size
            return (0, 0, w, h)
        return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    # Без numpy — медленный, но рабочий обход по PIL
    w, h = mask_l.size
    px = mask_l.load()
    x0, y0, x1, y1 = w, h, 0, 0
    found = False
    for y in range(h):
        for x in range(w):
            if px[x, y] > 127:
                found = True
                x0 = min(x0, x)
                y0 = min(y0, y)
                x1 = max(x1, x + 1)
                y1 = max(y1, y + 1)
    if not found:
        return (0, 0, w, h)
    return (x0, y0, x1, y1)


def _mask_area(mask_l: Image.Image) -> int:
    if _HAS_NUMPY:
        return int(np.count_nonzero(np.array(mask_l) > 127))
    w, h = mask_l.size
    px = mask_l.load()
    return sum(1 for y in range(h) for x in range(w) if px[x, y] > 127)


def _build_bbox_mask(
    size: tuple[int, int],
    bbox: tuple[int, int, int, int],
    feather: float,
) -> Image.Image:
    """Прямоугольник с мягкими краями через GaussianBlur (без OpenCV)."""
    w, h = size
    mask = Image.new("L", (w, h), 0)
    x0, y0, x1, y1 = bbox
    x0 = max(0, min(x0, w - 1))
    y0 = max(0, min(y0, h - 1))
    x1 = max(x0 + 1, min(x1, w))
    y1 = max(y0 + 1, min(y1, h))
    ImageDraw.Draw(mask).rectangle([x0, y0, x1 - 1, y1 - 1], fill=255)
    if feather > 0:
        # PIL radius ~ sigma; достаточно для плавного перехода inpaint
        mask = mask.filter(ImageFilter.GaussianBlur(radius=feather))
    return mask


def _build_color_mask(
    rgb: Image.Image,
    target_rgb: tuple[int, int, int],
    tolerance: float,
) -> Image.Image:
    """Простой порог по max(|channel-target|) как fallback сегментации ткани."""
    w, h = rgb.size
    if _HAS_NUMPY:
        arr = np.array(rgb, dtype=np.int16)
        target = np.array(target_rgb, dtype=np.int16)
        diff = np.abs(arr - target)
        dist = np.max(diff, axis=2)
        binary = (dist <= tolerance).astype(np.uint8) * 255
        return Image.fromarray(binary, mode="L")
    # PIL-only: tolerable только на маленьких кадрах
    out = Image.new("L", (w, h), 0)
    tr, tg, tb = target_rgb
    tol = int(tolerance)
    px_in = rgb.load()
    px_out = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b = px_in[x, y]
            if max(abs(r - tr), abs(g - tg), abs(b - tb)) <= tol:
                px_out[x, y] = 255
    return out


def build_mask(
    image_path: Path,
    output_dir: Path,
    *,
    mask_file: Path | None = None,
    bbox: tuple[int, int, int, int] | None = None,
    feather: float = 8.0,
    target_rgb: tuple[int, int, int] | None = None,
    color_tolerance: float = 28.0,
    color_feather: float = 4.0,
    task_id: str = "mask",
) -> MaskBuildResult:
    """Собрать маску из файла, bbox или порога по цвету (в таком приоритете).

    Returns:
        MaskBuildResult с путём PNG (L mode, белое = inpaint).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    rgb, size = _load_rgb(image_path)
    source: Literal["file", "bbox", "color"]
    meta: dict[str, Any] = {"image_size": list(size), "numpy_available": _HAS_NUMPY}

    if mask_file is not None:
        with Image.open(mask_file) as mf:
            mask_l = mf.convert("L")
        if mask_l.size != size:
            mask_l = mask_l.resize(size, Image.Resampling.NEAREST)
        source = "file"
        meta["mask_file"] = str(mask_file)
    elif bbox is not None:
        mask_l = _build_bbox_mask(size, bbox, feather)
        source = "bbox"
        meta["bbox_input"] = list(bbox)
        meta["feather"] = feather
    elif target_rgb is not None:
        mask_l = _build_color_mask(rgb, target_rgb, color_tolerance)
        if color_feather > 0:
            mask_l = mask_l.filter(ImageFilter.GaussianBlur(radius=color_feather))
        source = "color"
        meta["target_rgb"] = list(target_rgb)
        meta["color_tolerance"] = color_tolerance
    else:
        raise ValueError(
            "Нужен один источник маски: mask_file, bbox или target_rgb (+ color_tolerance)."
        )

    out_path = output_dir / f"{task_id}_fabric_mask.png"
    mask_l.save(out_path)
    box = _mask_bbox(mask_l)
    area = _mask_area(mask_l)
    meta["bbox_computed"] = list(box)
    sidecar = output_dir / f"{task_id}_mask_meta.json"
    sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return MaskBuildResult(
        path=out_path,
        bbox=box,
        area=area,
        source=source,
        metadata=meta,
    )