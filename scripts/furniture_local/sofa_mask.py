"""Маска обивки дивана для детерминированной перекраски в бордо-бархат.

Задача: отделить ВЯЗКУ (плед, бахрома), ПОДУШКИ и прочие объекты от
ОБИВКИ, которую нужно перекрасить. Никакой генерации — геометрия кадра
не меняется.

Ключевые принципы (выведены замерами, не на глаз):

1.  ТЕКСТУРА — главный признак материала.
    mean |Laplacian| в окне 9x9:
      плетёная вязка (плед)          tex p50 = 98..109
      гладкая обивка                   tex p50 = 3..9
      тень под пледом                  tex p50 = 11
    Порог KNIT_TEX = 22 разделяет нити и гладкую ткань.

2.  frac8 = доля пикселей с tex >= 22 в окне 8x8 (нормировка 0..1!).
    У настоящей вязки frac8 p50 = 0.9, у гладкой обивки 0.00..0.30.
    ОШИБКА, которая стоила 20 итераций: frac нормировался к 0..64
    и сравнивался с 0.30 — тогда в «вязку» попадал ЛЮБОЙ пиксель
    с текстурой в окне, и маска пледа была на 13 тыс. px больше нужного.

3.  Защита вязки = frac8 >= thr И tex >= 25. Одно условие недостаточно:
    окно 8x8 ловит ТЕНЬ под пледом (frac8 0.23, tex 11) — гладкая обивка
    под краем пледа оставалась серой.

4.  Перфокрытие: маска подушек/пледа расширяется ВНУТРЬ обивки, чтобы
    бордо доходило до объекта вплотную (иначе остаётся серая нитка).

5.  Ступенчатых краёв и «шахматки» быть не должно. Majority-фильтр
    7x7 даёт ровно «шахматный» узор — поэтому граница сглаживается
    Gaussian-размытием альфы при композите, а не фильтром по маске.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

# --- геометрия кадра (1280x960) ---
TOP_PROFILE = [(315, 370), (350, 364), (420, 360), (500, 359), (560, 358),
                (620, 359), (680, 360), (730, 362), (755, 367), (780, 361),
                (830, 359), (865, 361), (885, 366), (900, 374), (912, 388),
                (920, 402), (924, 410)]
BOTTOM_PROFILE = [(240, 776), (300, 784), (350, 787), (410, 790), (470, 795),
                  (560, 800), (640, 755), (700, 748), (760, 743), (820, 739),
                  (860, 735), (900, 732), (940, 700), (1010, 650)]

# Плед: узкая свисающая часть слева и широкая справа (Vision 6x + замеры)
BLANKET_NARROW = [(266, 524), (294, 508), (306, 540), (302, 625), (296, 675),
                   (285, 694), (266, 700), (250, 690), (250, 520)]
BLANKET_WIDE = [(443, 557), (455, 570), (480, 584), (515, 590), (550, 584),
                 (578, 599), (598, 615), (619, 653), (632, 695), (634, 730),
                 (626, 760), (611, 778), (590, 792), (540, 798), (500, 790),
                 (482, 735), (467, 680), (454, 622), (447, 580)]

# Подушки. Правый край зелёной — x=903 (замер: при x=905..915 тёмной
# подушки нет вовсе; прежний полигон до x=958 закрывал тёмную складку
# между подушками и она оставалась серой).
PILLOW_GREEN = [(806, 471), (812, 465), (826, 463), (848, 466), (858, 465),
                 (868, 459), (884, 448), (898, 452), (903, 458), (903, 520),
                 (898, 548), (884, 560), (860, 566), (838, 561), (820, 553),
                 (810, 540), (806, 510)]
# Нижняя граница светлой подушки — y=600 (а не 553): замеры показали, что
# полоса y=556..600 имеет warm=17..18 и sat=0.10..0.11, тогда как обивка
# дивана на солнце даёт warm=39/sat=0.19, в тени sat=0.21..0.37.
# То есть под «подушкой» лежит та же светлая ткань, и бордо туда падать
# не должно. Подтверждение: тёмная контактная тень идёт непрерывной
# линией по y=601..605 от x=890 до x=1015 — это тень ПОД подушкой.
PILLOW_LIGHT = [(938, 438), (974, 440), (1010, 446), (1041, 454), (1063, 468),
                (1070, 505), (1063, 537), (1048, 551), (1030, 562),
                (1019, 583), (1015, 600), (996, 601), (972, 601), (950, 600),
                (938, 597), (934, 570), (934, 520), (934, 470)]
PILLOW_OLIVE = [(868, 443), (902, 443), (902, 467), (868, 467)]
GREEN_TOP = [(806, 470), (820, 461), (850, 456), (880, 453), (910, 450),
              (940, 447), (958, 446), (962, 428), (798, 428)]

VASE = [(1014, 574), (1080, 574), (1080, 648), (1010, 648),
        (1012, 610), (1020, 592), (1030, 582)]
BOUQUET = [(1036, 520), (1058, 484), (1092, 484), (1114, 502), (1120, 534),
           (1112, 574), (1092, 604), (1060, 610), (1038, 592)]
TRIPOD = [(1092, 440), (1124, 440), (1150, 690), (1118, 690), (1104, 560),
          (1086, 480)]
WOOD = [(228, 478), (314, 478), (314, 524), (252, 530), (249, 800), (228, 800)]

KNIT_TEX = 22.0        # порог «пиксель принадлежит нитям»
KNIT_TEX_STRICT = 25.0  # порог для защиты вязки (жёстче, отсекает тень)
KNIT_FRAC = 0.40       # доля нитей в окне 8x8 для защиты вязки
KNIT_FRAC_CORE = 0.60  # уверенное ядро (гистерезис)


def _poly(shape, points):
    w, h = shape[1], shape[0]
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).polygon([(int(x), int(y)) for x, y in points], fill=255)
    return np.asarray(m) > 127


def _rect(shape, x1, y1, x2, y2):
    m = np.zeros(shape, bool)
    m[y1:y2, x1:x2] = True
    return m


def _profile(points, width):
    xs = np.arange(width)
    y = np.full(width, np.nan)
    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        lo, hi = min(x0, x1), max(x0, x1)
        m = (xs >= lo) & (xs <= hi)
        if hi > lo:
            y[m] = np.interp(xs[m], [x0, x1], [y0, y1])
        else:
            y[m] = y0
    return y


def _safe_fill(mask, forbidden):
    """Залить внутренние дыры, не заходя на запрещённые объекты."""
    filled = ndimage.binary_fill_holes(mask)
    holes = filled & ~mask
    if not holes.any():
        return mask
    labels, n = ndimage.label(holes)
    keep = np.zeros(n + 1, bool)
    for i in range(1, n + 1):
        comp = labels == i
        if not (comp & forbidden).any():
            keep[i] = True
    return mask | (keep[labels] & holes)


def build_masks(source_path):
    """Вернуть (selection, protect, stats) по исходному кадру.

    selection — пиксели обивки, которые перекрашиваются;
    protect   — пиксели, которые нельзя трогать (плед, подушки, ваза...).
    """
    src = np.asarray(Image.open(source_path).convert("RGB")).astype(np.float32)
    h, w = src.shape[:2]
    shape = src.shape[:2]

    lum = 0.299 * src[..., 0] + 0.587 * src[..., 1] + 0.114 * src[..., 2]
    warm = src[..., 0] - src[..., 2]
    mx = src.max(axis=2)
    mn = src.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)

    tex = ndimage.uniform_filter(np.abs(ndimage.laplace(lum)), size=9)
    frac8 = ndimage.uniform_filter((tex >= KNIT_TEX).astype(np.float32), size=8)
    fine = ndimage.uniform_filter(np.abs(ndimage.laplace(lum)), size=3)

    # --- объекты, которые не перекрашиваем ---
    green = _poly(shape, PILLOW_GREEN)
    light = _poly(shape, PILLOW_LIGHT)
    olive = _poly(shape, PILLOW_OLIVE)
    objects = (green | olive | light
               | _poly(shape, VASE) | _poly(shape, BOUQUET) | _poly(shape, TRIPOD)
               | _rect(shape, 1060, 440, 1084, 506)
               | _rect(shape, 1036, 590, 1066, 644)
               | _rect(shape, 1118, 330, 1280, 960)
               | _rect(shape, 1036, 440, 1080, 502))
    fringe = ndimage.binary_dilation(
        _rect(shape, 984, 552, 1046, 596) & (tex >= 22), iterations=2)
    objects = objects | fringe
    wood = _poly(shape, WOOD)

    # --- защита вязки: и плотное окно, и локальная текстура ---
    false_pos = (_rect(shape, 452, 498, 472, 518)
                 | _rect(shape, 357, 597, 428, 642)
                 | _rect(shape, 422, 638, 439, 655))
    knit = ((frac8 >= KNIT_FRAC) & (tex >= KNIT_TEX_STRICT)
            & _rect(shape, 246, 480, 646, 830))
    knit = knit & ~(false_pos & ~(fine >= KNIT_TEX))
    knit = ndimage.binary_closing(knit, iterations=1)
    knit = ndimage.binary_opening(knit, iterations=1)
    labels, n = ndimage.label(knit)
    keep = np.zeros(n + 1, bool)
    for i in range(1, n + 1):
        comp = labels == i
        if (comp & (frac8 >= KNIT_FRAC_CORE)).any():
            keep[i] = True
    knit = keep[labels]

    knit_right = ((frac8 >= KNIT_FRAC) & (tex >= KNIT_TEX_STRICT)
                  & _rect(shape, 820, 490, 1085, 650))
    knit_right = ndimage.binary_closing(knit_right, iterations=2)
    knit_right = ndimage.binary_opening(knit_right, iterations=1)
    knit = knit | knit_right

    geo = _poly(shape, BLANKET_NARROW) | _poly(shape, BLANKET_WIDE)
    strong = (frac8 >= 0.55) & (tex >= KNIT_TEX_STRICT)
    knit = knit | (geo & ndimage.binary_dilation(strong, iterations=6))

    # --- КРАЙ ПЛЕДА ---
    # 3 px вокруг вязки, но ТОЛЬКО там, где САМ пиксель похож на нить
    # (tex>=16). Порог именно по локальной текстуре, а не по frac8:
    # frac8 берёт окно 8x8, поэтому гладкая солнечная полоса рядом
    # с пледом (tex 6..10, frac8 0.62) считалась «вязкой». С порогом
    # tex>=16 в protect попадает 0 px гладкой обивки, а крапинки бордо
    # на краях пледа (505 px в 12 кластерах) уходят.
    knit_edge = (ndimage.binary_dilation(knit, iterations=3)
                 & (frac8 >= 0.35) & (tex >= 16))
    knit = knit | knit_edge

    protect = objects | knit
    # Низ дивана: запрет начинается с y=792, а не 782. Полоса
    # y=782..789 (x=370..437) — это последние пиксели обивки в тени
    # (lum 52->31, sat 0.35, tex 8), а не пол: пол ниже имеет lum<25.
    forbidden = protect | wood | _rect(shape, 290, 792, 470, 834)

    # --- силуэт дивана ---
    top_y = _profile(TOP_PROFILE, w)
    bottom_y = _profile(BOTTOM_PROFILE, w)
    rows = np.arange(h)[:, None]
    band = ((~np.isnan(top_y))[None, :]
            & (rows >= np.nan_to_num(top_y, nan=1e9)[None, :].astype(int))
            & (rows <= np.nan_to_num(bottom_y, nan=-1e9)[None, :].astype(int)))
    low_cut = np.zeros(shape, bool)
    for x in range(w):
        if not np.isnan(bottom_y[x]):
            low_cut[int(bottom_y[x]):, x] = True

    bright_neutral = (lum >= 178) & (sat <= 0.17) & ~((warm >= 26) & (sat >= 0.13))
    wall_hot = (lum >= 195) & (sat <= 0.15)

    sel = np.zeros(shape, bool)
    # Верхняя часть сиденья/спинки. sat<=0.42, а не 0.34: затенённая обивка
    # подлокотника имеет sat 0.356 и иначе оставалась серой.
    fabric = (warm >= 10) & (lum <= 215) & (sat <= 0.42) & (tex < 40)
    sel |= band & fabric & ~forbidden

    corner = _rect(shape, 316, 480, 640, 815)
    corner_fabric = (corner & ~knit & ~objects & ~wood & ~bright_neutral
                     & ~_rect(shape, 640, 700, 1280, 960)
                     & ~_rect(shape, 316, 790, 640, 960))
    sel |= corner_fabric & ~forbidden & ~low_cut

    above_green = _poly(shape, GREEN_TOP)
    above_ok = ((tex <= 26) & (sat <= 0.42) & (lum >= 55) & (lum <= 225)
                & (warm >= 8))
    sel |= (above_green & above_ok & ~forbidden & ~bright_neutral & ~wall_hot)

    # Тёмная складка между зелёной и светлой подушками
    crease = _rect(shape, 898, 452, 940, 572)
    crease_ok = (tex < 20) & (warm >= 6) & (sat <= 0.45) & (lum <= 215)
    sel |= crease & crease_ok & ~forbidden

    # Кольцо вокруг подушек — снимает порог sat<=0.32, который резал
    # полосу обивки слева от зелёной подушки (там sat=0.345 в тени).
    ring = ndimage.binary_dilation(green | olive | light, iterations=12) & ~objects
    ring_ok = ((tex <= 26) & (warm >= 6) & (sat <= 0.40)
               & (lum >= 30) & (lum <= 215))
    sel |= ring & ring_ok & ~forbidden & ~bright_neutral

    # Верхняя планка спинки: от белой стены её отличает насыщенность.
    top_rail = _rect(shape, 318, 348, 700, 376)
    top_ok = (warm >= 20) & (sat >= 0.12) & (lum >= 95) & (lum <= 215) & (tex < 40)
    sel |= top_rail & top_ok & ~forbidden

    right_zone = _rect(shape, 1019, 486, 1122, 640)
    right_ok = ((tex < 8.0) & (lum >= 95) & (lum <= 175)
                & (warm >= 12) & (warm <= 30) & (sat <= 0.21))
    sel |= right_zone & right_ok & ~forbidden

    under_green = _rect(shape, 878, 554, 928, 650)
    under_ok = ((tex < 17.0) & (warm >= 15) & (warm <= 42)
                & (sat >= 0.10) & (lum <= 180))
    sel |= under_green & under_ok & ~forbidden

    side = _rect(shape, 292, 545, 470, 792)
    side_ok = ((tex < 9.0) & (warm >= 8) & (warm <= 42)
               & (lum >= 38) & (lum <= 165))
    sel |= side & side_ok & ~forbidden

    skirt = _rect(shape, 600, 616, 1048, 662)
    skirt_ok = (warm >= 14) & (sat >= 0.10) & (lum <= 168) & (tex < 22)
    sel |= skirt & skirt_ok & ~forbidden

    base_right = _rect(shape, 985, 608, 1048, 674)
    base_ok = ((tex < 26) & (warm >= 10) & (warm <= 48)
               & (sat <= 0.30) & (lum <= 200))
    sel |= base_right & base_ok & ~forbidden

    mid_seat = _rect(shape, 925, 552, 1000, 584)
    mid_ok = ((tex < 20) & (warm >= 8) & (warm <= 50)
              & (sat <= 0.30) & (lum >= 100) & (lum <= 210))
    sel |= mid_seat & mid_ok & ~forbidden

    gap = _rect(shape, 888, 572, 1046, 620)
    gap_ok = ((tex < 20) & (warm >= 8) & (warm <= 52)
              & (sat <= 0.32) & (lum >= 60) & (lum <= 215))
    sel |= gap & gap_ok & ~forbidden

    # До-заполнение гладких «дыр» рядом с уже выбранной обивкой.
    def smooth_uph():
        return ((tex < 20) & (warm >= 8) & (warm <= 52)
                & (sat >= 0.04) & (sat <= 0.42)
                & (lum >= 40) & (lum <= 215))

    body = (band | corner | _rect(shape, 600, 560, 1048, 680)
            | _rect(shape, 246, 480, 646, 830))
    candidates = smooth_uph() & body & ~protect
    for _ in range(4):
        grow = candidates & ndimage.binary_dilation(sel, iterations=5) & ~protect
        if not grow.any():
            break
        sel = sel | grow

    sel &= ~forbidden & ~low_cut
    sel = ndimage.binary_closing(sel, iterations=2)
    sel = _safe_fill(sel, forbidden | low_cut)
    sel &= ~forbidden & ~low_cut
    for _ in range(2):
        labels, n = ndimage.label(sel)
        sizes = ndimage.sum(sel, labels, range(1, n + 1))
        keep = np.zeros(n + 1, bool)
        keep[1:] = sizes >= 400
        sel = keep[labels]
        sel = _safe_fill(sel, forbidden | low_cut)
        sel &= ~forbidden & ~low_cut

    # Солнечная обивка: яркая (lum 150..215) и ГЛАДКАЯ (tex<12) ткань.
    # Полоска x=545..579, y=575..590 (lum 187, warm 29, tex 9) — это
    # обивка в солнечном пятне между элементами пледа. Она оставалась
    # серой, т.к. была отдельным компонентом в 153 px и выбрасывалась
    # фильтром «компонент >= 400 px» выше. Поэтому правило применяется
    # ПОСЛЕ этого фильтра. Признаки обивки: warm>=24 (у белой стены
    # warm 8..21) и sat>=0.12 (у стены 0.095).
    sunlit = (warm >= 24) & (sat >= 0.12) & (sat <= 0.30) \
        & (lum >= 150) & (lum <= 215) & (tex < 12)
    sel |= sunlit & _rect(shape, 246, 480, 1048, 830) & ~forbidden & ~low_cut
    sel = ndimage.binary_closing(sel, iterations=2)
    sel = _safe_fill(sel, forbidden | low_cut)
    sel &= ~forbidden & ~low_cut

    # ---- СГЛАЖИВАНИЕ КОНТУРА ----
    # Контур по текстуре идёт попиксельно и выглядит «лесенкой»
    # (Vision: ступенчатые края). Сглаживаем в субпиксельном разрешении:
    # апскейл бинарной маски -> Gaussian -> порог. Защищённые объекты
    # после этого отнимаются обратно.
    up = ndimage.zoom(sel.astype(np.float32), 3, order=1)
    up = ndimage.gaussian_filter(up, sigma=1.5)
    smooth = ndimage.zoom(up, 1.0 / 3, order=1) > 0.5
    if smooth.shape == sel.shape:
        smooth &= ~forbidden
        smooth |= sel & ~forbidden
        sel = smooth

    stats = {
        "mask_px": int(sel.sum()),
        "protect_px": int(protect.sum()),
        "knit_px": int(knit.sum()),
        "objects_px": int(objects.sum()),
    }
    return sel, protect, stats


def feather_alpha(selection, protect, feather_px=1.2, grow_px=3, supersample=4):
    """Альфа для композита: 1 внутри обивки, 0 на объектах, мягкий край.

    Граница сглаживается в СУБПИКСЕЛЬНОМ разрешении (апскейл маски в
    supersample раз + Gaussian + даунскейл). Прямое размытие бинарной
    маски в 1x даёт «лесенку»: контур идёт по пикселям, и на границе
    с белой стеной видны ступени 1-2 px.

    Majority-фильтр по маске (7x7) для этой задачи НЕЛЬЗЯ: он даёт
    упорядоченный «шахматный» узор в заливке.

    Маска перед этим расширяется на grow_px внутрь обивки (но не на
    объекты), чтобы бордо доходило до пледа/подушки вплотную.
    """
    sel = selection.copy()
    free = ~protect
    # Расширение разрешено только по ткани обивки: иначе бордо заходит
    # на белую стену и пол (замер: 7864 px изменений вне маски).
    src = np.asarray(Image.open(_source_ref[0]).convert("RGB")).astype(np.float32)
    lum = 0.299 * src[..., 0] + 0.587 * src[..., 1] + 0.114 * src[..., 2]
    warm = src[..., 0] - src[..., 2]
    mx = src.max(axis=2)
    mn = src.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    tex = ndimage.uniform_filter(np.abs(ndimage.laplace(lum)), size=9)
    fabric = (warm >= 8) & (warm <= 52) & (sat <= 0.42) & (tex < 40) & (lum <= 215)
    grown = ndimage.binary_dilation(sel, iterations=grow_px) & free & fabric
    sel = sel | grown

    # --- субпиксельное покрытие контура ---
    # Покрытие считается как доля субпикселей, попавших в закрашиваемую
    # область (апскейл nearest + даунскейл). Так контур сглаживается без
    # «лесенки».
    up = ndimage.zoom(sel.astype(np.float32), supersample, order=0)
    cover = ndimage.uniform_filter(up, size=supersample)[:sel.shape[0], :sel.shape[1]]
    # Сглаживание КОНТУРА (не самой маски): апскейл бинарной маски ->
    # Gaussian -> нормировка. Покрытие берётся по сглаженному контуру,
    # поэтому граница округлая, а закрашенная область не теряет пикселей.
    up1 = ndimage.zoom(sel.astype(np.float32), supersample, order=1)
    up1 = ndimage.gaussian_filter(up1, sigma=0.9 * supersample / 2.0)
    smooth = ndimage.zoom(up1, 1.0 / supersample, order=1)
    if smooth.shape != sel.shape:
        smooth = smooth[:sel.shape[0], :sel.shape[1]]
    smooth = np.clip(smooth, 0.0, 1.0)
    smooth[protect] = 0.0

    # ВНУТРИ закрашиваемой области альфа = 1: никакого смешивания бордо
    # со старой тканью. Иначе у пледа и подушек появляется серо-коричневый
    # кант в несколько пикселей — на 1x он читается как «остатки старой
    # обивки» (Vision 1x и 8x).
    alpha = np.zeros(sel.shape, np.float32)
    alpha[sel] = 1.0
    # СНАРУЖИ — субпиксельное покрытие: граница получается мягкой без
    # «лесенки». Смешивание идёт с тканью, а не с пледом/подушкой
    # (там альфа строго 0).
    outside = (~sel) & fabric & (~protect)
    alpha[outside] = np.maximum(cover[outside], smooth[outside] * 0.5)
    # На объектах — строго 0 (плед, подушки, ваза, букет).
    alpha[protect] = 0.0
    # Вне ткани обивки (стена, пол, ковёр, дерево) — 0.
    # Вне ткани обивки (стена, пол, ковёр, дерево) — 0. Правило ТОЛЬКО
    # снаружи маски: иначе пиксели обивки, не прошедшие текстурный
    # фильтр, остались бы серыми внутри заливки.
    alpha[(~sel) & (~fabric)] = 0.0
    return alpha


# Путь к исходнику для расчёта ткани в feather_alpha (задаётся рендером).
_source_ref = [None]


def set_source_reference(path):
    """Сообщить feather_alpha, где исходный кадр (для проверки ткани)."""
    _source_ref[0] = str(path)
