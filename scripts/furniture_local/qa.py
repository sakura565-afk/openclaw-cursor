"""Автоматическая приёмка: силуэт, цвет, швы + коллаж «референс | результат»."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from scripts.eval_judge.metrics import laplacian


@dataclass(frozen=True)
class QAThresholds:
    """Пороги владельца: вне маски почти не должно меняться."""

    max_outside_change_fraction: float = 0.025
    min_inside_change_fraction: float = 0.04
    max_mean_abs_diff_outside: float = 6.0
    min_gradient_correlation: float = 0.55
    max_inside_lab_shift: float = 18.0


@dataclass
class QAReport:
    verdict: str
    passed: bool
    metrics: dict[str, float]
    reasons: list[str]
    collage_path: Path
    text_report: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["collage_path"] = str(self.collage_path)
        return d


def _rgb_array(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.float64)


def _mask_bool(path: Path, size: tuple[int, int]) -> np.ndarray:
    with Image.open(path) as im:
        mask = im.convert("L")
    if mask.size != size:
        mask = mask.resize(size, Image.Resampling.NEAREST)
    return np.asarray(mask) > 127


def _to_gray(rgb: np.ndarray) -> np.ndarray:
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def _mean_abs_diff(a: np.ndarray, b: np.ndarray, region: np.ndarray) -> float:
    if not np.any(region):
        return 0.0
    diff = np.abs(a - b)
    if diff.ndim == 3:
        diff = diff.mean(axis=2)
    return float(diff[region].mean())


def _changed_fraction(a: np.ndarray, b: np.ndarray, region: np.ndarray, thresh: float = 4.0) -> float:
    if not np.any(region):
        return 0.0
    diff = np.abs(a - b)
    if diff.ndim == 3:
        diff = diff.max(axis=2)
    changed = (diff > thresh) & region
    return float(changed.sum() / region.sum())


def _rgb_to_lab_simple(rgb: np.ndarray) -> np.ndarray:
    """Упрощённый Lab без scipy — для оценки сдвига оттенка ткани."""
    r, g, b = rgb[..., 0] / 255.0, rgb[..., 1] / 255.0, rgb[..., 2] / 255.0
    def lin(c: np.ndarray) -> np.ndarray:
        return np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    R, G, B = lin(r), lin(g), lin(b)
    X = 0.4124 * R + 0.3576 * G + 0.1805 * B
    Y = 0.2126 * R + 0.7152 * G + 0.0722 * B
    Z = 0.0193 * R + 0.1192 * G + 0.9505 * B
    Xn, Yn, Zn = X / 0.95047, Y / 1.0, Z / 1.08883
    eps = 216 / 24389
    kappa = 24389 / 27
    def f(t: np.ndarray) -> np.ndarray:
        return np.where(t > eps, np.cbrt(t), (kappa * t + 16) / 116)
    fx, fy, fz = f(Xn), f(Yn), f(Zn)
    L = 116 * fy - 16
    a = 500 * (fx - fy)
    b_ch = 200 * (fy - fz)
    return np.stack([L, a, b_ch], axis=-1)


def _gradient_correlation(ref_gray: np.ndarray, out_gray: np.ndarray, region: np.ndarray) -> float:
    """Сохранение структуры швов: корреляция лапласианов внутри маски."""
    if not np.any(region):
        return 1.0
    lap_r = laplacian(ref_gray)
    lap_o = laplacian(out_gray)
    r = lap_r[region].astype(np.float64).ravel()
    o = lap_o[region].astype(np.float64).ravel()
    if r.size < 16:
        return 1.0
    r = r - r.mean()
    o = o - o.mean()
    denom = np.sqrt((r * r).sum() * (o * o).sum())
    if denom < 1e-9:
        return 1.0
    return float((r * o).sum() / denom)


def build_collage(
    reference_path: Path,
    result_path: Path,
    output_path: Path,
    *,
    verdict: str = "",
    label_left: str = "референс",
    label_right: str = "результат",
) -> Path:
    """Бок о бок — обязательный артефакт приёмки."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(reference_path) as ref, Image.open(result_path) as res:
        ref_i = ref.convert("RGB")
        res_i = res.convert("RGB")
        h = max(ref_i.height, res_i.height)
        if ref_i.height != h:
            ref_i = ref_i.resize((int(ref_i.width * h / ref_i.height), h), Image.Resampling.LANCZOS)
        if res_i.height != h:
            res_i = res_i.resize((int(res_i.width * h / res_i.height), h), Image.Resampling.LANCZOS)
        gap = 8
        header = 36
        width = ref_i.width + gap + res_i.width
        canvas = Image.new("RGB", (width, h + header), (24, 24, 28))
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 20)
        except OSError:
            font = ImageFont.load_default()
        draw.text((12, 8), label_left, fill=(220, 220, 220), font=font)
        draw.text((ref_i.width + gap + 12, 8), label_right, fill=(220, 220, 220), font=font)
        if verdict:
            color = (80, 200, 120) if verdict.upper() == "PASS" else (240, 100, 100)
            draw.text((width - 80, 8), verdict.upper(), fill=color, font=font)
        canvas.paste(ref_i, (0, header))
        canvas.paste(res_i, (ref_i.width + gap, header))
        canvas.save(output_path)
    return output_path


def evaluate_acceptance(
    reference_path: Path,
    result_path: Path,
    mask_path: Path,
    collage_path: Path,
    *,
    thresholds: QAThresholds | None = None,
    sample_rgb: tuple[int, int, int] | None = None,
) -> QAReport:
    """PASS/FAIL по метрикам + коллаж и текстовый отчёт."""
    th = thresholds or QAThresholds()
    ref = _rgb_array(reference_path)
    out = _rgb_array(result_path)
    if ref.shape != out.shape:
        raise ValueError(
            f"reference {ref.shape} и result {out.shape} должны совпадать по размеру"
        )
    h, w = ref.shape[:2]
    inside = _mask_bool(mask_path, (w, h))
    outside = ~inside

    inside_change = _changed_fraction(ref, out, inside)
    outside_change = _changed_fraction(ref, out, outside)
    outside_mad = _mean_abs_diff(ref, out, outside)
    ref_gray = _to_gray(ref)
    out_gray = _to_gray(out)
    grad_corr = _gradient_correlation(ref_gray, out_gray, inside)

    lab_ref = _rgb_to_lab_simple(ref)
    lab_out = _rgb_to_lab_simple(out)
    inside_lab_shift = float(np.linalg.norm(lab_ref[inside] - lab_out[inside], axis=-1).mean())

    metrics = {
        "inside_mask_change_fraction": inside_change,
        "outside_mask_change_fraction": outside_change,
        "outside_mean_abs_diff": outside_mad,
        "inside_lab_shift": inside_lab_shift,
        "gradient_correlation": grad_corr,
    }

    reasons: list[str] = []
    if outside_change > th.max_outside_change_fraction:
        reasons.append(
            f"Нарушение силуэта: доля изменённых пикселей вне маски "
            f"{outside_change:.3f} > {th.max_outside_change_fraction:.3f}"
        )
    if outside_mad > th.max_mean_abs_diff_outside:
        reasons.append(
            f"Сильные изменения вне маски (MAD={outside_mad:.2f} > {th.max_mean_abs_diff_outside})"
        )
    if inside_change < th.min_inside_change_fraction:
        reasons.append(
            f"Мало изменений в зоне ткани: {inside_change:.3f} < {th.min_inside_change_fraction:.3f}"
        )
    if grad_corr < th.min_gradient_correlation:
        reasons.append(
            f"Структура швов/фактуры не сохранена: corr={grad_corr:.3f} < {th.min_gradient_correlation:.3f}"
        )
    if inside_lab_shift > th.max_inside_lab_shift and sample_rgb is None:
        reasons.append(
            f"Сильный сдвиг оттенка в маске: Lab Δ={inside_lab_shift:.1f} > {th.max_inside_lab_shift}"
        )

    passed = len(reasons) == 0
    verdict = "PASS" if passed else "FAIL"
    collage_path.parent.mkdir(parents=True, exist_ok=True)
    build_collage(reference_path, result_path, collage_path, verdict=verdict)

    lines = [
        f"Вердикт: {verdict}",
        f"Метрики: {json.dumps(metrics, ensure_ascii=False)}",
    ]
    if reasons:
        lines.append("Причины FAIL:")
        lines.extend(f"  - {r}" for r in reasons)
    else:
        lines.append("Силуэт вне маски сохранён; изменения локализованы в обивке.")
    text_report = "\n".join(lines)

    return QAReport(
        verdict=verdict,
        passed=passed,
        metrics=metrics,
        reasons=reasons,
        collage_path=collage_path,
        text_report=text_report,
    )


def write_qa_artifacts(report: QAReport, output_dir: Path, stem: str = "qa") -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{stem}_report.json"
    txt_path = output_dir / f"{stem}_report.txt"
    json_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(report.text_report, encoding="utf-8")
    return {"json": json_path, "txt": txt_path, "collage": report.collage_path}