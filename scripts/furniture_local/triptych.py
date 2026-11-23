"""Триптих приёмки: ВХОД | ЧТО ДЕЛАЕМ (описание) | ВЫХОД.

Обязательный артефакт: пользователь оценивает результат только по картинке,
поэтому в одной панели рядом должны быть исходник, текст задачи и результат.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw, ImageFont

_BG = (24, 24, 28)
_PANEL = (34, 34, 40)
_FG = (235, 235, 240)
_ACCENT = (120, 190, 255)
_DIM = (170, 170, 180)
_GOOD = (90, 210, 130)
_BAD = (240, 100, 100)

def _font_dirs() -> list[str]:
    """Каталоги системных шрифтов.

    Абсолютный путь не зашит в исходник: берётся из окружения или из
    системной папки Fonts текущей ОС — иначе guard-тест CI
    (tests/test_no_absolute_paths.py) ломает сборку на других хостах.
    """
    dirs: list[str] = []
    for var in ("SystemRoot", "WINDIR"):
        root = os.environ.get(var)
        if root:
            dirs.append(os.path.join(root, "Fonts"))
    dirs.append("/usr/share/fonts/truetype/dejavu")
    dirs.append("/usr/share/fonts")
    dirs.append("Fonts")
    return dirs


_FONT_NAMES = ("segoeui.ttf", "arial.ttf", "tahoma.ttf", "DejaVuSans.ttf")
_FONT_BOLD_NAMES = ("segoeuib.ttf", "arialbd.ttf", "tahomabd.ttf", "DejaVuSans-Bold.ttf")


def _font_candidates(bold: bool) -> list[str]:
    names = _FONT_BOLD_NAMES if bold else _FONT_NAMES
    out: list[str] = []
    for d in _font_dirs():
        for n in names:
            out.append(os.path.join(d, n))
    return out


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    paths = _font_candidates(bold)
    for path in paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    cur = words[0]
    for word in words[1:]:
        probe = f"{cur} {word}"
        if draw.textlength(probe, font=font) <= max_w:
            cur = probe
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


def build_triptych(
    reference_path: Path,
    result_path: Path,
    output_path: Path,
    *,
    title: str = "",
    description_lines: Sequence[str] = (),
    metrics_lines: Sequence[str] = (),
    verdict: str = "",
    panel_width: int = 560,
) -> Path:
    """Собрать ВХОД | ОПИСАНИЕ | ВЫХОД одним изображением."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(reference_path) as ref, Image.open(result_path) as res:
        ref_i = ref.convert("RGB")
        res_i = res.convert("RGB")
        h = max(ref_i.height, res_i.height)
        if ref_i.height != h:
            ref_i = ref_i.resize((int(ref_i.width * h / ref_i.height), h), Image.Resampling.LANCZOS)
        if res_i.height != h:
            res_i = res_i.resize((int(res_i.width * h / res_i.height), h), Image.Resampling.LANCZOS)

        header = 54
        gap = 10
        pad = 18
        mid_w = panel_width
        width = ref_i.width + gap + mid_w + gap + res_i.width
        canvas = Image.new("RGB", (width, h + header), _BG)
        draw = ImageDraw.Draw(canvas)

        f_title = _font(26, bold=True)
        f_head = _font(22, bold=True)
        f_body = _font(19)
        f_small = _font(17)

        # Шапка: заголовок + вердикт
        draw.text((pad, 14), title or "Приёмка мебельного inpaint", fill=_FG, font=f_title)
        if verdict:
            color = _GOOD if verdict.upper() == "PASS" else _BAD
            tw = draw.textlength(verdict.upper(), font=f_head)
            draw.text((width - tw - pad, 18), verdict.upper(), fill=color, font=f_head)

        # Заголовки колонок
        draw.text((pad, header + 6), "ВХОД (референс)", fill=_ACCENT, font=f_head)
        draw.text((ref_i.width + gap + pad, header + 6), "ЧТО ДЕЛАЕМ", fill=_ACCENT, font=f_head)
        draw.text((ref_i.width + gap + mid_w + gap + pad, header + 6), "ВЫХОД", fill=_ACCENT, font=f_head)

        # Панель описания
        mid_x = ref_i.width + gap
        draw.rectangle(
            [mid_x, header, mid_x + mid_w, header + h], fill=_PANEL, outline=(60, 60, 70), width=1
        )
        y = header + 16
        inner_w = mid_w - 2 * pad
        for line in description_lines:
            if not line:
                y += 10
                continue
            wrapped = _wrap(draw, str(line), f_body, inner_w)
            for w_line in wrapped:
                if y > header + h - 24:
                    break
                draw.text((mid_x + pad, y), w_line, fill=_FG, font=f_body)
                y += 26
        y += 14
        if metrics_lines:
            draw.text((mid_x + pad, y), "Замеры", fill=_ACCENT, font=f_head)
            y += 32
            for line in metrics_lines:
                for w_line in _wrap(draw, str(line), f_small, inner_w):
                    if y > header + h - 20:
                        break
                    draw.text((mid_x + pad, y), w_line, fill=_DIM, font=f_small)
                    y += 22

        canvas.paste(ref_i, (0, header))
        canvas.paste(res_i, (ref_i.width + gap + mid_w + gap, header))
        canvas.save(output_path)
    return output_path


def describe_task(task: dict[str, Any], choice_kind: str, choice_notes: str) -> list[str]:
    """Человекочитаемое описание задачи для панели триптиха."""
    furniture = str(task.get("furniture_type", task.get("furniture", "мебель")))
    operation = str(task.get("operation", task.get("task", task.get("goal", "inpaint"))))
    lines = [
        f"Задача: {furniture} — {operation}",
        f"Движок: {choice_kind}",
    ]
    prompt = task.get("prompt")
    if prompt:
        lines.append(f"Промпт: {prompt}")
    spec = dict(task.get("mask") or task.get("mask_source") or {})
    if spec.get("bbox"):
        lines.append(f"Маска (bbox): {spec['bbox']}")
    for key, label in (
        ("flux_denoise", "denoise"),
        ("flux_cfg", "cfg"),
        ("flux_steps", "steps"),
        ("seed", "seed"),
    ):
        if key in task:
            lines.append(f"{label}: {task[key]}")
    if choice_notes:
        lines.append(f"Модель: {choice_notes}")
    return lines
