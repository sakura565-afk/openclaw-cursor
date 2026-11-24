"""Рендер бордо-бархата по маске обивки: детерминированно, без генерации.

Светлота исходного кадра сохраняется (тени, складки, швы, пуговицы,
фактура), меняется только цвет. Цвет берётся из эталона владельца:
кривая «медианный RGB эталона -> Lab по бакам светлоты» + модель блика
бархата (на солнечной стороне хроматичность ПАДАЕТ, а не растёт).

Композит — мягкий: альфа по расстоянию до границы маски (см.
sofa_mask.feather_alpha). Бордо подходит к пледу/подушке вплотную,
серой нитки не остаётся, и ступенчатой «лесенки» нет.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from .color_calibrate import srgb_to_lab
from .sofa_mask import feather_alpha, set_source_reference
from .velvet_recolor import velvet_recolor

# Параметры подобраны свипом по метрикам (см. отчёт):
#   серость ярких освещённых участков 54% -> 0%
#   оттенок 343° (эталон 337°), насыщенность 0.63 (эталон 0.67)
COLOR_PARAMS = dict(
    l_base=20.0,
    contrast_keep=0.65,
    chroma_gain=0.92,
    b_shift=3.0,
    hi_decay=0.35,
    hi_span=14.0,
    sheen_start=34.0,
    sheen_span=10.0,
    sheen_decay=0.5,
    sheen_b=7.0,
    l_lo=12.0,
    l_hi=32.0,
)


def recolor_sofa(
    source_path,
    etalon_path,
    etalon_mask_path,
    output_path,
    mask=None,
    protect=None,
    color_params=None,
):
    """Перекрасить обивку и вернуть метрики."""
    from .sofa_mask import build_masks

    source_path = Path(source_path)
    etalon_path = Path(etalon_path)
    etalon_mask_path = Path(etalon_mask_path)
    output_path = Path(output_path)

    if mask is None or protect is None:
        mask, protect, _ = build_masks(source_path)
    params = dict(COLOR_PARAMS)
    if color_params:
        params.update(color_params)

    src = np.asarray(Image.open(source_path).convert("RGB")).astype(np.float32)
    # Перекраска считается для всего кадра, а показывается только по маске:
    # так край получается без «ступенек» — пиксели за границей уже имеют
    # корректный цвет ткани, а не серую подложку.
    full = np.ones(src.shape[:2], bool)
    tmp_mask = output_path.parent / "_recolor_all.png"
    velvet_recolor(
        source_path, _write_mask(tmp_mask, full), etalon_path, etalon_mask_path,
        output_path.parent / "_recolor_all_result.png", **params)
    recolored = np.asarray(
        Image.open(output_path.parent / "_recolor_all_result.png").convert("RGB")
    ).astype(np.float32)

    set_source_reference(source_path)
    alpha = feather_alpha(mask, protect)[..., None]
    out = np.clip(src * (1.0 - alpha) + recolored * alpha, 0, 255).astype(np.uint8)
    Image.fromarray(out).save(output_path)

    changed = np.abs(out.astype(np.float32) - src).max(axis=2) > 6
    res = out.astype(np.float32)
    mx = res.max(axis=2)
    mn = res.min(axis=2)
    d = mx - mn
    hue = np.zeros_like(mx)
    v = d > 1e-6
    R, G, B = res[..., 0], res[..., 1], res[..., 2]
    mm, rr, gg, bb, ds = mx[v], R[v], G[v], B[v], d[v]
    hue[v] = np.where(mm == rr, (60 * ((gg - bb) / ds) + 360) % 360,
                      np.where(mm == gg, (60 * ((bb - rr) / ds) + 120) % 360,
                               (60 * ((rr - gg) / ds) + 240) % 360))
    sat = np.where(mx > 0, d / np.maximum(mx, 1), 0)
    lum_src = 0.299 * src[..., 0] + 0.587 * src[..., 1] + 0.114 * src[..., 2]
    bright = mask & (lum_src > 140)
    magenta = mask & (hue > 320) & (hue < 356) & (sat > 0.4)

    metrics = {
        "result_median_rgb": [round(float(v), 1) for v in np.median(res[mask], axis=0)],
        "result_hue_deg": round(float(np.median(hue[mask])), 1),
        "result_sat_p50": round(float(np.percentile(sat[mask], 50)), 3),
        "R_minus_B": round(float((R - B)[mask].mean()), 1),
        "grey_share_bright_pct": round(
            float(100 * (bright & (sat < 0.35)).sum() / max(bright.sum(), 1)), 1),
        "magenta_share_all_pct": round(float(magenta.sum() / max(mask.sum(), 1) * 100), 1),
        "changed_outside_mask_px": int(changed[~mask].sum()),
        "mask_px": int(mask.sum()),
        "edge_px": int(((alpha[..., 0] > 0) & (alpha[..., 0] < 1)).sum()),
    }
    return metrics


def _write_mask(path: Path, mask: np.ndarray) -> Path:
    Image.fromarray((mask * 255).astype(np.uint8)).save(path)
    return path
