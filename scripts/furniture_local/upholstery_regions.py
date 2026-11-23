"""Маска обивки для кадра, где обивка совпадает по цвету с комнатой.

Проблема: на части кадров обивка того же светло-серо-бежевого тона, что и
белая кирпичная стена, деревянная тумба и плед. Цветовая маска их не
разделяет — нужен набор независимых признаков и явная геометрия.

Признаки (замерено на реальных кадрах, разброс отличается от кадра к кадру,
поэтому все пороги — начальные, а не константы):
  * теплота warm = R - B: обивка тёплая (+18..+40), кирпичная стена
    холоднее (+13..+27), дерево горячее (+45..+65);
  * насыщенность: обивка 0.20..0.47, обесцвеченная стена 0.06..0.13;
  * текстура mean|Laplacian| 9x9: гладкая обивка 3..9, вязаный плед
    12..55, кирпичная кладка 29;
  * яркий нейтральный фон (lum>=178 и sat<=0.17) — стена/окно/занавес.

Что НЕ работает (проверено):
  * один общий SAM-bbox: захватывает соседние объекты;
  * GrabCut: сжимает спинку на ~6%, потому что GMM стены и ткани близки;
  * QDA/color-rule: либо режет обивку, либо не отделяет кирпич;
  * грубая анизотропия: то же самое.

Рабочая схема:
  1. SAM по ОТДЕЛЬНЫМ регионам (спинка / сиденье / подлокотник / боковина),
     а не по одному общему прямоугольнику;
  2. из выбранных SAM-областей вырезаются посторонние объекты явными
     полигонами (подушки, ваза, букет, плед, дерево);
  3. сверх SAM дорисовываются участки, которые он пропустил (швы между
     сегментами спинки, верхняя рейка на солнце, боковина под пледом) —
     по контурам, снятым крупным планом, а не по цвету;
  4. верхняя и нижняя кромки задаются ПРОФИЛЯМИ ПО x: солнечная
     кирпичная стена над спинкой неотличима от обивки по цвету
     (warm 26..38 у обоих), поэтому там работает только геометрия.

Модуль содержит только переиспользуемые примитивы; конкретные полигоны
задаются вызывающим кодом.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage


def poly_mask(polygons, shape) -> np.ndarray:
    """Полигоны -> булева маска (shape = (H, W))."""
    m = Image.new("L", (shape[1], shape[0]), 0)
    d = ImageDraw.Draw(m)
    for p in polygons:
        d.polygon([(float(x), float(y)) for x, y in p], fill=255)
    return np.asarray(m) > 127


def rect(shape, x1, y1, x2, y2) -> np.ndarray:
    m = np.zeros(shape, bool)
    m[y1:y2, x1:x2] = True
    return m


def features(rgb: np.ndarray):
    """Признаки кадра: светлота, насыщенность, теплота, текстура."""
    a = rgb.astype(np.float32)
    lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    warm = a[..., 0] - a[..., 2]
    tex = ndimage.uniform_filter(np.abs(ndimage.laplace(lum)), size=9)
    return lum, sat, warm, tex


def profile_map(profile, width: int):
    """Профиль «(x, y) -> y» -> массив y по каждому x (np.nan вне профиля)."""
    xs = np.arange(width)
    y = np.full(width, np.nan)
    for i in range(len(profile) - 1):
        x0, y0 = profile[i]
        x1, y1 = profile[i + 1]
        lo, hi = min(x0, x1), max(x0, x1)
        m = (xs >= lo) & (xs <= hi)
        y[m] = np.interp(xs[m], [x0, x1], [y0, y1]) if hi > lo else y0
    return y


def band_between(top_profile, bottom_profile, width: int, height: int) -> np.ndarray:
    """Полоса между двумя профилями по x (например, спинка)."""
    xs = np.arange(width)
    top_y = profile_map(top_profile, width)
    bot_y = profile_map(bottom_profile, width)
    rows = np.arange(height)[:, None]
    valid = ~np.isnan(top_y)
    return valid[None, :] & \
        (rows >= np.nan_to_num(top_y, nan=1e9)[None, :].astype(int)) & \
        (rows <= np.nan_to_num(bot_y, nan=-1e9)[None, :].astype(int))


def cut_below(profile, width: int, height: int) -> np.ndarray:
    """Область НИЖЕ профиля — для обрезки spill на пол/ковёр."""
    y = profile_map(profile, width)
    out = np.zeros((height, width), bool)
    for x in range(width):
        if not np.isnan(y[x]):
            out[int(y[x]):, x] = True
    return out


def safe_fill(s: np.ndarray, forbidden: np.ndarray) -> np.ndarray:
    """Залить внутренние дыры, НЕ заходя на запрещённые области.

    binary_fill_holes целиком заливает всё, включая дыры с подушками внутри
    (заливка поднимала протечку до 100%). Здесь заливаются только те
    компоненты, которые целиком свободны.
    """
    filled = ndimage.binary_fill_holes(s)
    holes = filled & ~s
    if not holes.any():
        return s
    lab, n = ndimage.label(holes)
    keep = np.zeros(n + 1, bool)
    for i in range(1, n + 1):
        comp = lab == i
        if not (comp & forbidden).any():
            keep[i] = True
    return s | (keep[lab] & holes)


def drop_small_components(s: np.ndarray, min_px: int = 2000) -> np.ndarray:
    lab, n = ndimage.label(s)
    if n == 0:
        return s
    sizes = ndimage.sum(s, lab, range(1, n + 1))
    keep = np.zeros(n + 1, bool)
    keep[1:] = sizes >= min_px
    return keep[lab]


def finalize(mask: np.ndarray, forbidden: np.ndarray, min_px: int = 2000) -> np.ndarray:
    """Закрытие -> безопасная заливка -> отсечение мелких компонент."""
    mask = mask & ~forbidden
    mask = ndimage.binary_closing(mask, iterations=2)
    mask = safe_fill(mask, forbidden)
    mask = mask & ~forbidden
    for _ in range(2):
        mask = drop_small_components(mask, min_px)
        mask = safe_fill(mask, forbidden)
        mask = mask & ~forbidden
    return mask


def verify_outside(src: np.ndarray, res: np.ndarray, mask: np.ndarray, tol: int = 0) -> int:
    """Сколько пикселей вне маски изменилось — должно быть 0."""
    d = np.abs(src.astype(int) - res.astype(int)).max(axis=2)
    return int((d[~mask] > tol).sum())
