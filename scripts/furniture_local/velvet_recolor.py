"""Перекраска обивки в бордо-бархат: кривая по МЕДИАННОМУ RGB эталона.

Корень дефекта «малиновый/фуксиевый» найден замерами:
  * кривая строилась по СРЕДНЕМУ a* в баке на выборке «пиксели с a*>6»;
    из-за выбросов a* доходил до 37 при L=27, что даёт Lab вне sRGB-gamut
    (G = -71). lab_to_srgb режет каналы, G обнулялся — бордо становилось
    фуксией;
  * теперь кривая строится по МЕДИАННОМУ RGB в каждом баке светлоты
    эталона: медиана реальных пикселей по построению лежит ВНУТРИ гаммы,
    поэтому обрезки каналов не происходит;
  * серый каркас стула (L>36) в кривую не входит: для светлее L_max
    хроматичность плавно затухает — так же, как блики бархата в эталоне
    (a* 25 при L=33 против 6 при L=40);
  * проверка gamut — по конверсии БЕЗ обрезки, иначе она всегда «проходит».

Светлота исходного кадра сохраняется по контрасту: тени, складки, швы,
пуговицы и фактура остаются исходными — меняется только цвет.
"""
import numpy as np
from PIL import Image
from scipy import ndimage

from .color_calibrate import lab_to_srgb, srgb_to_lab


def _lab_to_srgb_raw(lab):
    """Lab -> sRGB БЕЗ обрезки (честная проверка gamut)."""
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0
    eps = 216 / 24389.0
    kappa = 24389 / 27.0

    def finv(t):
        t3 = t ** 3
        return np.where(t3 > eps, t3, (116 * t - 16) / kappa)

    X = finv(fx) * 0.95047
    Y = np.where(L > 8.0, ((L + 16) / 116.0) ** 3, L / kappa)
    Z = finv(fz) * 1.08883
    r = X * 3.2406 + Y * -1.5372 + Z * -0.4986
    g = X * -0.9689 + Y * 1.8758 + Z * 0.0415
    bl = X * 0.0557 + Y * -0.2040 + Z * 1.0570

    def gamma(c):
        ac = np.abs(c)
        s = np.sign(c)
        return np.where(ac <= 0.0031308, c * 12.92, 1.055 * np.power(ac, 1 / 2.4) * s - 0.055)

    return np.stack([gamma(r), gamma(g), gamma(bl)], axis=-1) * 255.0


def _gamut_fit(L, a, b):
    """Уменьшить хроматичность до попадания в sRGB-gamut (бинарный поиск)."""
    def fits(scale):
        rgb = _lab_to_srgb_raw(np.stack([L, a * scale, b * scale], axis=-1))
        return np.all((rgb >= 0.0) & (rgb <= 255.0), axis=-1)

    ok = fits(np.ones_like(a))
    scale = np.where(ok, 1.0, 0.0)
    bad = ~ok
    if bad.any():
        blo = np.zeros(int(bad.sum()))
        bhi = np.ones(int(bad.sum()))
        for _ in range(20):
            mid = (blo + bhi) / 2
            rgb = _lab_to_srgb_raw(np.stack([L[bad], a[bad] * mid, b[bad] * mid], axis=-1))
            good = np.all((rgb >= 0.0) & (rgb <= 255.0), axis=-1)
            bhi = np.where(good, mid, bhi)
            blo = np.where(good, blo, mid)
        scale[bad] = bhi
    return scale


def etalon_color_curve(etalon_path, etalon_mask_path, wine_max_l=36.0, n_bins=10):
    """Кривая эталона: median RGB -> Lab по бакам светлоты.

    Возвращает (grid, a_of_L, b_of_L, L_min, L_max).
    """
    etalon = np.asarray(Image.open(etalon_path).convert("RGB")).astype(np.float64)
    emask = np.asarray(Image.open(etalon_mask_path).convert("L")) > 127
    lab = srgb_to_lab(etalon)
    L, A = lab[..., 0], lab[..., 1]
    wine = emask & (L < wine_max_l) & (A > 3.0)
    if wine.sum() < 500:
        wine = emask & (L < wine_max_l)
    Ls = L[wine]
    L_min = float(np.percentile(Ls, 2))
    L_max = float(np.percentile(Ls, 98))
    edges = np.linspace(L_min, L_max, n_bins + 1)
    xs, ya, yb = [], [], []
    for i in range(n_bins):
        hi_edge = edges[i + 1] + (1e-9 if i == n_bins - 1 else 0)
        s = wine & (L >= edges[i]) & (L < hi_edge)
        if s.sum() < 20:
            continue
        med = np.median(etalon[s], axis=0)
        lab_med = srgb_to_lab(med)
        xs.append(float((edges[i] + edges[i + 1]) / 2))
        ya.append(float(lab_med[1]))
        yb.append(float(lab_med[2]))
    xs, ya, yb = np.array(xs), np.array(ya), np.array(yb)
    grid = np.linspace(L_min, L_max, 128)
    a_of_L = ndimage.uniform_filter1d(np.interp(grid, xs, ya), 5)
    b_of_L = ndimage.uniform_filter1d(np.interp(grid, xs, yb), 5)
    a_of_L[:6] = np.interp(grid[:6], xs, ya)[:6]
    a_of_L[-6:] = np.interp(grid[-6:], xs, ya)[-6:]
    b_of_L[:6] = np.interp(grid[:6], xs, yb)[:6]
    b_of_L[-6:] = np.interp(grid[-6:], xs, yb)[-6:]
    return grid, a_of_L, b_of_L, L_min, L_max


def velvet_recolor(
    source_path,
    mask_path,
    etalon_path,
    etalon_mask_path,
    output_path,
    l_base=30.0,
    contrast_keep=0.55,
    l_lo=12.0,
    l_hi=46.0,
    chroma_gain=1.0,
    hi_decay=0.45,
    hi_span=16.0,
    b_shift=0.0,
    hue_shift=0.0,
    sheen_start=30.0,
    sheen_span=12.0,
    sheen_decay=0.75,
    sheen_b=6.0,
):
    """Перекрасить обивку под бордо-бархат эталона, сохранив структуру."""
    src = np.asarray(Image.open(source_path).convert("RGB")).astype(np.float64)
    mask = np.asarray(Image.open(mask_path).convert("L")) > 127
    grid, a_of_L, b_of_L, L_min, L_max = etalon_color_curve(etalon_path, etalon_mask_path)

    lab = srgb_to_lab(src)
    L_orig = lab[..., 0]
    L_mean = float(L_orig[mask].mean())

    L_new = l_base + contrast_keep * (L_orig - L_mean)
    over = np.clip(L_new - l_hi, 0.0, None)
    L_new = L_new - over * (over / (over + 4.0))
    under = np.clip(l_lo - L_new, 0.0, None)
    L_new = L_new + under * (under / (under + 4.0)) * 0.7
    L_new = np.clip(L_new, 0.0, 100.0)

    A_new = np.interp(L_new, grid, a_of_L) * chroma_gain
    B_new = np.interp(L_new, grid, b_of_L) * chroma_gain

    # оттенок: поворот в Lab вокруг оси L (для «коричнево-красного» вместо
    # малинового достаточно сдвинуть b* вверх; hue_shift — поворот, если
    # нужно увести оттенок к красному)
    if b_shift:
        B_new = B_new + b_shift
    if hue_shift:
        ang = np.arctan2(B_new, A_new) + np.radians(hue_shift)
        ch = np.hypot(A_new, B_new)
        A_new = ch * np.cos(ang)
        B_new = ch * np.sin(ang)

    # БЛЕСК БАРХАТА: у настоящего ворса на солнечной стороне появляется
    # пыльно-розовый блик — хроматичность ПАДАЕТ, а не растёт. Без этого
    # модель даёт фуксию: светлые участки уходят в малиновый.
    # (В эталоне владельца то же: a* 33 при L=29, но a* 21 при L=39.)
    sheen = np.clip((L_new - sheen_start) / max(sheen_span, 1e-6), 0, 1)
    A_new = A_new * (1.0 - sheen_decay * sheen)
    B_new = B_new * (1.0 - sheen_decay * sheen) + sheen_b * sheen

    above = np.clip(L_new - L_max, 0.0, None)
    decay = 1.0 - hi_decay * np.clip(above / hi_span, 0, 1)
    below = np.clip(L_min - L_new, 0.0, None)
    decay = decay * (1.0 - 0.30 * np.clip(below / 12.0, 0, 1))
    A_new = A_new * decay
    B_new = B_new * decay

    out = lab.copy()
    out[..., 0] = L_new
    out[..., 1] = A_new
    out[..., 2] = B_new

    scale = _gamut_fit(out[..., 0][mask], out[..., 1][mask], out[..., 2][mask])
    full = np.ones(src.shape[:2])
    full[mask] = scale
    out[..., 1] *= full
    out[..., 2] *= full

    rgb = lab_to_srgb(out)
    mixed = src.copy()
    mixed[mask] = np.clip(rgb[mask], 0, 255)
    Image.fromarray(mixed.astype(np.uint8)).save(output_path)

    res = mixed
    mx = res.max(axis=2)
    mn = res.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    R, G, B = res[..., 0], res[..., 1], res[..., 2]
    return {
        "median_rgb": [round(float(v), 1) for v in np.median(res[mask], axis=0)],
        "sat_p50": round(float(np.percentile(sat[mask], 50)), 3),
        "R_minus_B": round(float((R - B)[mask].mean()), 1),
        "G_min": round(float(G[mask].min()), 1),
        "L_p50": round(float(np.percentile(L_new[mask], 50)), 1),
        "L_p5_p95": [round(float(np.percentile(L_new[mask], 5)), 1),
                     round(float(np.percentile(L_new[mask], 95)), 1)],
        "clipped_px": int((res[mask].min(axis=1) < 1).sum()),
        "changed_outside_mask_px": int((np.abs(res - src).max(axis=2) > 6)[~mask].sum()),
        "gamut_scaled_pct": round(float((scale < 0.98).mean() * 100), 1),
        "etalon_wine_L": [round(L_min, 1), round(L_max, 1)],
    }
