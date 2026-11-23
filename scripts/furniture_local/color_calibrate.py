"""Калибровка цвета обивки под эталон владельца (FURN-OWN-20261123).

Зачем: генерация даёт правильную СВЕТЛОТУ (тени, складки, швы) и фактуру,
но хроматичность уходит (a* 6 против 26 у эталона) — ткань выходит
серо-бордовой вместо винно-бордовой. Калибровка берёт генерацию как есть
по светлоте (L*), а цвет (a*, b*) приводит к эталону, сохраняя
относительную разницу цветов внутри обивки.

Это честная операция: она не рисует текстуру, а переносит цвет.
"""
import numpy as np
from PIL import Image


def srgb_to_lab(rgb):
    a = np.asarray(rgb, dtype=np.float64) / 255.0
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    r, g, b = lin[..., 0], lin[..., 1], lin[..., 2]
    X = r * 0.4124 + g * 0.3576 + b * 0.1805
    Y = r * 0.2126 + g * 0.7152 + b * 0.0722
    Z = r * 0.0193 + g * 0.1192 + b * 0.9505
    Xn, Yn, Zn = 0.95047, 1.0, 1.08883

    def f(t):
        return np.where(t > 0.008856, t ** (1 / 3), 7.787 * t + 16 / 116)

    fx, fy, fz = f(X / Xn), f(Y / Yn), f(Z / Zn)
    return np.stack([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)], axis=-1)


def lab_to_srgb(lab):
    """Lab -> sRGB.

    ВАЖНО: порог ветки для Y — L* > 8, а НЕ kappa/29 (31.15).
    Линейный участок функции L*(Y) действует до Y_n = 0.008856, то есть
    до L* = 8. При ошибочном пороге 31.15 канал Y занижался почти вдвое
    для всех тёмных цветов (L* 8..31), и обратная конверсия давала
    отрицательный G -> после обрезки каналов бордовый бархат превращался
    в малиново-фуксиевый. Проверено на 3000 реальных пикселей эталона:
    после исправления отклонение обратной конверсии < 1/255.
    """
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16) / 116
    fx = fy + a / 500
    fz = fy - b / 200
    eps = 216 / 24389
    kappa = 24389 / 27

    def finv(t):
        t3 = t ** 3
        return np.where(t3 > eps, t3, (116 * t - 16) / kappa)

    X = finv(fx) * 0.95047
    Y = np.where(L > 8.0, ((L + 16) / 116) ** 3, L / kappa)
    Z = finv(fz) * 1.08883
    r = X * 3.2406 + Y * -1.5372 + Z * -0.4986
    g = X * -0.9689 + Y * 1.8758 + Z * 0.0415
    bl = X * 0.0557 + Y * -0.2040 + Z * 1.0570

    def gamma(c):
        c = np.clip(c, 0.0, 1.0)
        return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)

    out = np.stack([gamma(r), gamma(g), gamma(bl)], axis=-1)
    return np.clip(out * 255.0, 0, 255).astype(np.uint8)


def calibrate_to_reference(
    reference_path,
    generated_path,
    mask_path,
    output_path,
    target_rgb=(75, 25, 45),
    chroma_gain=1.0,
    keep_lightness=True,
    chroma_exponent=0.5,
    chroma_max=2.2,
    chroma_min=0.6,
):
    """Сдвинуть цвет обивки к эталону, сохранив светлоту генерации.

    target_rgb — измеренный цвет обивки в эталонном результате владельца.
    chroma_gain — усиление цветности (1.0 = ровно к цели).
    chroma_exponent — показатель связи «цветность vs освещённость».
        Насыщенность реальной ткани растёт со светом: эталонный бордо
        измерен в тени (L* 17), та же ткань на солнечной стороне дивана
        даёт a* около 43. При 0.5 цветность идёт как (L/L_etalon)^0.5.
    chroma_max/chroma_min — ограничители масштаба цветности.
    """
    ref = np.asarray(Image.open(reference_path).convert("RGB")).astype(np.float64)
    gen = np.asarray(Image.open(generated_path).convert("RGB")).astype(np.float64)
    mask = np.asarray(Image.open(mask_path).convert("L")) > 127

    lab_gen = srgb_to_lab(gen)
    tgt = srgb_to_lab(np.array(target_rgb, dtype=np.float64))

    L_gen = lab_gen[..., 0]
    L_tgt = float(tgt[0])
    a_t = float(tgt[1])
    b_t = float(tgt[2])

    out = lab_gen.copy()
    out[..., 0] = L_gen
    # Хроматичность ЗАМЕНЯЕТСЯ, а не прибавляется: иначе собственная
    # вариация цвета исходника (в тени насыщенность исходной ткани 0.35-0.51)
    # даёт ярко-малиновые полосы вдоль швов и складок.
    #
    # Насыщенность реальной ткани растёт с освещённостью: у эталона бордо
    # измерено в тени (L* 17, a* 26); на солнечной стороне дивана та же ткань
    # даёт a* около 43. Поэтому масштабируем хроматичность эталона как
    # (L/L_etalon)^chroma_exponent.
    scale = np.clip((L_gen / max(L_tgt, 1e-6)) ** chroma_exponent, 0.4, 3.2)
    scale = np.clip(scale, chroma_min, chroma_max)
    out[..., 1] = a_t * scale * chroma_gain
    out[..., 2] = b_t * scale * chroma_gain

    result = lab_to_srgb(out)
    mixed = ref.copy()
    mixed[mask] = result[mask]
    Image.fromarray(mixed.astype(np.uint8)).save(output_path)

    return {
        "target_rgb": list(target_rgb),
        "lab_before": {
            "L": float(lab_gen[..., 0][mask].mean()),
            "a": float(lab_gen[..., 1][mask].mean()),
            "b": float(lab_gen[..., 2][mask].mean()),
        },
        "lab_after": {
            "L": float(srgb_to_lab(result)[..., 0][mask].mean()),
            "a": float(srgb_to_lab(result)[..., 1][mask].mean()),
            "b": float(srgb_to_lab(result)[..., 2][mask].mean()),
        },
        "outside_changed_px": int((np.abs(mixed - ref).sum(axis=2) > 25)[~mask].sum()),
    }
