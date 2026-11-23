"""Кроп-режим inpaint: рендерить 768x768 нативным кропом, не апскейля.

Зачем (рецепт-эталон из ТЗ FURN-SRC-20261122):
  SDXL/Flux-инпейнт на 768x768, взятом НАТИВНО из оригинала (без увеличения),
  работает заметно лучше, чем прогон на кадре 1264x848: те же швы и
  текстура, но пикселей меньше — меньше шума и артефактов.

Замеры на реальных фото (диван 1264x848):
  768x768 + сплошная маска   -> B-R в ткани +41.7 (обивка стала синей), 30 с
  1264x848 + маска-полоса    -> B-R -16.9 (не перекрасила), 300 с
Вывод: рендерить надо кропом 768x768, и маска на генерацию должна быть
СПЛОШНОЙ (весь кроп), а точные границы обеспечивает paste-back.

ТАЙЛЫ: если область обивки шире 768 px (диван — 955 px), одного кропа мало.
compute_tiles режет область на перекрывающиеся квадраты 768x768; каждый
рендерится отдельно и вклеивается обратно в полный кадр.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image


def _square_crop_box(
    bbox: tuple[int, int, int, int], size: tuple[int, int], target: int
) -> tuple[int, int, int, int]:
    """Квадратный кроп target×target, содержащий bbox и вписанный в кадр.

    Сторона ВСЕГДА равна target (или меньше, если кадр меньше) — иначе
    кроп выходит не квадратным и restore_crop рассинхронизируется.
    """
    w, h = size
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    side = min(target, min(w, h))
    nx0 = max(0, min(cx - side // 2, w - side))
    ny0 = max(0, min(cy - side // 2, h - side))
    return nx0, ny0, nx0 + side, ny0 + side


def compute_tiles(
    bbox: tuple[int, int, int, int],
    size: tuple[int, int],
    target: int = 768,
    overlap: int = 96,
) -> list[tuple[int, int, int, int]]:
    """Разрезать область на квадраты target×target с перекрытием.

    Вертикально — один тайл (по центру bbox). Горизонтально — столько,
    сколько нужно, чтобы покрыть bbox целиком.
    """
    w, h = size
    x0, y0, x1, y1 = bbox
    side = min(target, min(w, h))
    cy = (y0 + y1) // 2
    ny0 = max(0, min(cy - side // 2, h - side))
    ny1 = ny0 + side

    span = x1 - x0
    if span <= side:
        nx0 = max(0, min(x0 - (side - span) // 2, w - side))
        return [(nx0, ny0, nx0 + side, ny1)]

    step = side - overlap
    tiles: list[tuple[int, int, int, int]] = []
    start = x0
    while True:
        nx0 = max(0, min(start, w - side))
        nx1 = nx0 + side
        tiles.append((nx0, ny0, nx1, ny1))
        if nx1 >= x1:
            break
        start += step
        if len(tiles) > 12:  # предохранитель
            break
    return tiles


def prepare_crop(
    reference_path: Path,
    mask_path: Path,
    output_dir: Path,
    *,
    target: int = 768,
    bbox: tuple[int, int, int, int] | None = None,
    stem: str = "crop",
) -> dict:
    """Нарезать 768x768 кроп исходника и маски. Вернуть координаты."""
    output_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(reference_path) as im:
        ref = im.convert("RGB")
    with Image.open(mask_path) as im:
        mask = im.convert("L")
    if mask.size != ref.size:
        mask = mask.resize(ref.size, Image.Resampling.NEAREST)

    if bbox is None:
        import numpy as np

        arr = np.asarray(mask)
        ys, xs = np.nonzero(arr > 127)
        if xs.size == 0:
            bbox = (0, 0, ref.width, ref.height)
        else:
            bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

    x0, y0, x1, y1 = _square_crop_box(bbox, ref.size, target)
    ref_crop = ref.crop((x0, y0, x1, y1))
    mask_crop = mask.crop((x0, y0, x1, y1))
    if ref_crop.width != target or ref_crop.height != target:
        # достраиваем до квадрата нужного размера без апскейла содержимого
        canvas = Image.new("RGB", (target, target), (255, 255, 255))
        canvas.paste(ref_crop, ((target - ref_crop.width) // 2, (target - ref_crop.height) // 2))
        ref_crop = canvas
        mcanvas = Image.new("L", (target, target), 0)
        mcanvas.paste(mask_crop, ((target - mask_crop.width) // 2, (target - mask_crop.height) // 2))
        mask_crop = mcanvas

    ref_path = output_dir / f"{stem}_768.png"
    mask_path_out = output_dir / f"{stem}_mask_768.png"
    ref_crop.save(ref_path)
    mask_crop.save(mask_path_out)
    return {
        "ref": str(ref_path),
        "mask": str(mask_path_out),
        "box": [x0, y0, x1, y1],
        "size": [ref_crop.width, ref_crop.height],
        "original_size": [ref.width, ref.height],
    }


def restore_crop(
    crop_result_path: Path,
    reference_path: Path,
    crop_box: list[int],
    output_path: Path,
) -> Path:
    """Вклеить результат кропа обратно в полный кадр оригинала."""
    with Image.open(reference_path) as im:
        full = im.convert("RGB")
    with Image.open(crop_result_path) as im:
        crop = im.convert("RGB")
    x0, y0, x1, y1 = crop_box
    if crop.size != (x1 - x0, y1 - y0):
        crop = crop.resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    full.paste(crop, (x0, y0))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    full.save(output_path)
    return output_path
