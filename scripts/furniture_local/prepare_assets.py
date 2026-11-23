"""Подготовка входов для мебельного inpaint по ТЗ FURN-SRC-20261122.

Для каждого предмета:
  crop      — нативный кроп 768x768 (без апскейла), рецепт-эталон
  mask      — ТОЧНАЯ маска обивки (по цвету ткани), по ней идёт склейка и приёмка
  fill_mask — СПЛОШНАЯ маска для генерации; fill-модели нужна цельная область,
              на дробной маске Flux Fill не перекрашивает вовсе

Запуск: python -m scripts.furniture_local.prepare_assets <config.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.furniture_local.upholstery_mask import build_upholstery_mask  # noqa: E402


def _hue_sat(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    dd = mx - mn
    hue = np.zeros(mx.shape, dtype=np.float32)
    ok = dd > 1e-6
    if ok.any():
        v, rr, gg, bb, d = mx[ok], r[ok], g[ok], b[ok], dd[ok]
        hue[ok] = np.where(
            v == rr,
            (60 * ((gg - bb) / d) + 360) % 360,
            np.where(v == gg, (60 * ((bb - rr) / d) + 120) % 360, (60 * ((rr - gg) / d) + 240) % 360),
        )
    sat = np.where(mx > 0, dd / np.maximum(mx, 1), 0)
    return hue, sat


def crop_box_for(bbox: list[int], size: tuple[int, int], target: int = 768) -> list[int]:
    """Квадратный кроп target×target вокруг bbox, вписанный в кадр."""
    w, h = size
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    side = min(target, min(w, h))
    nx0 = max(0, min(cx - side // 2, w - side))
    ny0 = max(0, min(cy - side // 2, h - side))
    return [nx0, ny0, nx0 + side, ny0 + side]


def prepare(
    *,
    source: Path,
    out_dir: Path,
    stem: str,
    region_bbox: list[int],
    mask_bbox: list[int],
    lum_lo: float,
    lum_hi: float,
    sat_max: float,
    hue_lo: float,
    hue_hi: float,
    fill_bands: list[list[int]],
    exclude_hue: tuple[float, float] | None = None,
    exclude_sat: float = 0.18,
    exclude_dilate: int = 5,
    target: int = 768,
) -> dict:
    """Нарезать кроп, построить точную маску обивки и сплошную fill-маску."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as im:
        full = im.convert("RGB")
    size = full.size
    box = crop_box_for(region_bbox, size, target)
    x0, y0, x1, y1 = box
    crop = full.crop((x0, y0, x1, y1))

    crop_path = out_dir / f"{stem}_crop768.png"
    crop.save(crop_path)

    # Точная маска обивки — по цвету ткани. Нужна в двух проекциях:
    #   precise_full   — полный кадр (для приёмки QA на полном кадре)
    #   mask_precise   — кроп (для paste-back, который идёт внутри кропа)
    precise_full = out_dir / f"{stem}_precise_full.png"
    info = build_upholstery_mask(
        source,
        tuple(mask_bbox),
        precise_full,
        lum_lo=lum_lo,
        lum_hi=lum_hi,
        sat_max=sat_max,
        hue_lo=hue_lo,
        hue_hi=hue_hi,
        feather=0,
        min_component_px=300,
    )
    with Image.open(precise_full) as im:
        pm = im.convert("L").crop((x0, y0, x1, y1))
    precise_path = out_dir / f"{stem}_mask_precise.png"
    pm.filter(ImageFilter.GaussianBlur(4)).save(precise_path)

    # СПЛОШНАЯ fill-маска на весь кроп.
    #
    # Замерено на реальных фото: fill-модель (Flux Fill) перекрашивает только
    # тогда, когда маска — цельная большая область.
    #   768x768 + сплошная маска      -> B-R в ткани +41.7 (обивка стала синей)
    #   768x768 + дробная маска       -> B-R -12.0 (обивка осталась бежевой)
    #   1264x848 + сплошная маска     -> B-R -16.9 (не перекрасила)
    # Точные границы обивки обеспечивает НЕ fill-маска, а paste-back по
    # precise-маске: он вклеивает сгенерированное только в пиксели ткани.
    fill_path = out_dir / f"{stem}_mask_fill.png"
    Image.new("L", crop.size, 255).save(fill_path)

    return {
        "crop": str(crop_path),
        "mask_precise": str(precise_path),
        "mask_fill": str(fill_path),
        "crop_box": box,
        "precise_area": int((np.asarray(Image.open(precise_path)) > 127).sum()),
        "fill_area": int((np.asarray(Image.open(fill_path)) > 127).sum()),
        "mask_info": info,
    }


def main(argv: list[str]) -> int:
    cfg = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    out = Path(cfg["out_dir"])
    results = {}
    for name, spec in cfg["items"].items():
        results[name] = prepare(
            source=Path(spec["source"]),
            out_dir=out,
            stem=name,
            region_bbox=spec["region_bbox"],
            mask_bbox=spec["mask_bbox"],
            lum_lo=spec["lum_lo"],
            lum_hi=spec["lum_hi"],
            sat_max=spec["sat_max"],
            hue_lo=spec["hue_lo"],
            hue_hi=spec["hue_hi"],
            fill_bands=spec["fill_bands"],
            exclude_hue=tuple(spec["exclude_hue"]) if spec.get("exclude_hue") else None,
            exclude_sat=float(spec.get("exclude_sat", 0.18)),
            exclude_dilate=int(spec.get("exclude_dilate", 5)),
            target=int(spec.get("target", 768)),
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
