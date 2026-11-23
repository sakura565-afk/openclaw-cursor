"""Paste-back: наложить сгенерированный результат на референс по маске.

Flux Fill (и любой полнокадровый inpaint при denoise=1.0) перерисовывает весь кадр.
Чтобы сохранить силуэт и фон, результат вклеивается в референс по маске:
внутри маски — сгенерированные пиксели, снаружи — оригинал.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def paste_back(
    reference_path: Path,
    generated_path: Path,
    mask_path: Path,
    output_path: Path,
    *,
    feather: float = 6.0,
    erode_px: int = 2,
) -> dict[str, float]:
    """Склеить результат с референтом по маске. Возвращает метрики склейки."""
    ref = Image.open(reference_path).convert("RGB")
    gen = Image.open(generated_path).convert("RGB")
    if gen.size != ref.size:
        gen = gen.resize(ref.size, Image.Resampling.LANCZOS)

    mask = Image.open(mask_path).convert("L")
    if mask.size != ref.size:
        mask = mask.resize(ref.size, Image.Resampling.LANCZOS)

    # Граница блендинга должна совпадать с границей, которую считает QA (mask > 127),
    # иначе размытая кромка задевает пиксели "вне маски" и ломает приёмку.
    hard = mask.point(lambda v: 255 if v >= 128 else 0)
    if erode_px > 0:
        hard = hard.filter(ImageFilter.MinFilter(erode_px * 2 + 1))
    if feather > 0:
        blurred = hard.filter(ImageFilter.GaussianBlur(radius=feather))
        # alpha строго внутри маски: сглаженный край, но ноль снаружи
        soft = Image.fromarray(
            np.minimum(np.asarray(blurred), np.asarray(hard)).astype(np.uint8)
        )
    else:
        soft = hard

    alpha = (np.asarray(soft, dtype=np.float32) / 255.0)[..., None]
    r = np.asarray(ref, dtype=np.float32)
    g = np.asarray(gen, dtype=np.float32)
    out = np.clip(r * (1.0 - alpha) + g * alpha, 0, 255).astype(np.uint8)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out).save(output_path)

    hard_bool = np.asarray(hard) > 127
    outside = ~hard_bool
    diff = np.abs(out.astype(np.float32) - r).sum(axis=2)
    metrics = {
        "pasteback_outside_change_fraction": float((diff[outside] > 25).mean()),
        "pasteback_outside_mad": float(np.abs(out[outside].astype(np.float32) - r[outside]).mean()),
        "pasteback_inside_change_fraction": float((diff[hard_bool] > 25).mean()),
    }
    return metrics
