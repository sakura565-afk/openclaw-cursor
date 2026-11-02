# -*- coding: utf-8 -*-
"""v2 skin finishing, built from the researched expert recipe.

Research summary (Kimi subagent, sources in the draft):
  - unsharp only amplifies existing high frequencies; if the model erased pores there is
    nothing to amplify -> the real gain comes from DONOR high-frequency texture grafting
  - frequency separation: low band = volume/tone/colour, high band = pores/hair
  - Dodge & Burn is a small, heavily blurred EXPOSURE map (+/-0.03-0.08 EV), not a strong
    local-contrast boost - over-doing it produces the "plastic" look
  - colour work in OKLab/OKLCh with robust stats (median, 1.4826*MAD) and a partial
    Reinhard adaptation (lambda 0.2-0.4) applied to a,b only, leaving L alone
  - two low-frequency Gaussian random fields on a/b restore the natural colour variation
    that a flat RGB "warmth" push destroys
  - specular breakup: highlights receive only ~30% of the texture effect
  - grain is the LAST step, global and monochrome (std 0.003-0.008, blur 0.4-0.7 px,
    x0.3 in highlights); it removes sterility/banding but never replaces pore texture
  - texture order: texture -> upscale -> grain last (BCNodes)

Pipeline here:
  1. donor high-frequency grafting: real skin relief cut into patches, randomly rotated /
     mirrored / cross-faded, so no visible repeat
  2. D&B as a blurred exposure map (linear RGB *= 2**d, d clamped to +/-0.05 EV)
  3. OKLab colour: robust a,b statistics + partial Reinhard + two random variation fields
  4. specular breakup (highlights get 30% of the texture)
  5. grain last, global, monochrome, luminance-weighted
"""
from __future__ import annotations

import argparse
import os

import numpy as np
from scipy.ndimage import gaussian_filter
from PIL import Image

EPS = 1e-6

# ---------------------------------------------------------------- colour spaces
M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
               [0.2119034982, 0.6806995451, 0.1073969566],
               [0.0883024619, 0.2817188376, 0.6299787005]])
M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
               [1.9779984951, -2.4285922050, 0.4505937099],
               [0.0259040371, 0.7827717662, -0.8086757660]])


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.clip(c, 0, None) ** (1 / 2.4) - 0.055)


def linear_to_oklab(rgb: np.ndarray) -> np.ndarray:
    lms = np.einsum("...j,ij->...i", rgb, M1)
    lms = np.cbrt(np.clip(lms, 0, None))
    return np.einsum("...j,ij->...i", lms, M2)


def oklab_to_linear(lab: np.ndarray) -> np.ndarray:
    lms = np.einsum("...j,ij->...i", lab, np.linalg.inv(M2))
    lms = lms ** 3
    return np.einsum("...j,ij->...i", lms, np.linalg.inv(M1))


# ---------------------------------------------------------------- helpers
def gauss(a: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return a
    if a.ndim == 3:
        return gaussian_filter(a, sigma=(sigma, sigma, 0), mode="nearest")
    return gaussian_filter(a, sigma=(sigma, sigma), mode="nearest")


def robust_stats(v: np.ndarray) -> tuple[float, float]:
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med)))
    return med, max(1.4826 * mad, EPS)


def skin_mask(rgb: np.ndarray) -> np.ndarray:
    """Skin-chroma mask, measured on our graded frames (see skin_detail_shade.py)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 0.5 * (b - y) + 0.5
    cr = 0.5 * (r - y) + 0.5
    m = np.clip((cb - 0.33) / 0.05, 0, 1) * np.clip((0.56 - cb) / 0.05, 0, 1)
    m *= np.clip((cr - 0.50) / 0.04, 0, 1) * np.clip((0.72 - cr) / 0.06, 0, 1)
    m *= np.clip((r - g - 0.09) / 0.04, 0, 1)
    m *= np.clip((g - b + 0.02) / 0.05, 0, 1) * np.clip((0.32 - (g - b)) / 0.08, 0, 1)
    m *= np.clip((y - 0.05) / 0.08, 0, 1)
    return m


# ---------------------------------------------------------------- texture grafting
def donor_highfreq(donor_rgb: np.ndarray, sigma: float = 2.0) -> np.ndarray:
    """Zero-mean, unit-RMS high-frequency of a real skin photo (Y channel)."""
    y = 0.299 * donor_rgb[..., 0] + 0.587 * donor_rgb[..., 1] + 0.114 * donor_rgb[..., 2]
    hp = y - gauss(y, sigma)
    hp -= hp.mean()
    rms = float(np.sqrt((hp ** 2).mean())) + EPS
    return (hp / rms).astype(np.float32)


def graft_texture(target_y: np.ndarray, donors: list[np.ndarray], mask: np.ndarray,
                  amount: float = 0.012, patch: int = 96, seed: int = 7) -> np.ndarray:
    """Paste donor high-frequency as randomly rotated/mirrored patches with cross-fade.

    Patching (instead of tiling the whole reference) is what removes the "stamped
    pattern" look: each patch has a different orientation, and edges are feathered.
    """
    h, w = target_y.shape
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w), dtype=np.float32)
    acc = np.zeros((h, w), dtype=np.float32)
    y0 = 0
    while y0 < h:
        y1 = min(y0 + patch, h)
        x0 = 0
        while x0 < w:
            x1 = min(x0 + patch, w)
            d = donors[int(rng.integers(len(donors)))]
            dh, dw = d.shape
            if dh < 16 or dw < 16:
                break
            ph, pw = y1 - y0, x1 - x0
            sy = int(rng.integers(0, max(1, dh - ph)))
            sx = int(rng.integers(0, max(1, dw - pw)))
            tile = d[sy:sy + ph, sx:sx + pw]
            if tile.shape != (ph, pw):
                break
            k = int(rng.integers(4))
            if k == 1:
                tile = tile[:, ::-1]
            elif k == 2:
                tile = tile[::-1, :]
            elif k == 3:
                tile = tile[::-1, ::-1]
            # feathered cross-fade weights
            fy = np.minimum(np.arange(ph) + 1, np.arange(ph)[::-1] + 1).astype(np.float32)
            fx = np.minimum(np.arange(pw) + 1, np.arange(pw)[::-1] + 1).astype(np.float32)
            wgt = np.outer(fy, fx)
            wgt /= wgt.max()
            wgt = np.clip(wgt * 1.6, 0, 1) ** 2
            out[y0:y1, x0:x1] += tile * wgt
            acc[y0:y1, x0:x1] += wgt
            x0 = x1
        y0 = y1
    out = np.divide(out, np.maximum(acc, EPS))
    # target's own high-frequency (the "D" term) so we boost existing structure too
    d_hp = target_y - gauss(target_y, 2.0)
    d_hp -= d_hp.mean()
    d_hp /= (float(np.sqrt((d_hp ** 2).mean())) + EPS)
    return d_hp, out


# ---------------------------------------------------------------- main pipeline
def retouch(src: str, dst: str, *, donors: list[str] = (), donor_amount: float = 0.012,
            ref: str | None = None,
            db_ev: float = 0.05, db_sigma: float = 40.0,
            warm_a: float = 3.0, adapt_lambda: float = 0.30,
            var_amount: float = 0.0025, var_sigma: float = 55.0,
            spec_break: float = 0.30,
            sss_mix: float = 0.06, sss_sigma: float = 4.0,
            grain: float = 0.005, grain_blur: float = 0.5, seed: int = 11):
    rgb = np.asarray(Image.open(src).convert("RGB")).astype(np.float32) / 255.0
    h, w, _ = rgb.shape
    lin = srgb_to_linear(rgb)

    Y = 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]

    # ---- 1. texture grafting (donor HF) ---------------------------------------
    mask = gauss(skin_mask(rgb), 15)[..., None]
    if donors and donor_amount > 0:
        dlist = [donor_highfreq(np.asarray(Image.open(p).convert("RGB")).astype(np.float32) / 255.0)
                 for p in donors if os.path.exists(p)]
        if dlist:
            d_hp, t_donor = graft_texture(Y, dlist, mask[..., 0], donor_amount)
            # specular breakup: highlights get only ~30% of the texture
            q85, q97 = np.percentile(Y, 85), np.percentile(Y, 97)
            S = np.clip((Y - q85) / max(q97 - q85, EPS), 0, 1)
            S = S * S * (3 - 2 * S)
            gain = np.where(S > 0, 1.0 - (1.0 - spec_break) * S, 1.0)
            rel = 0.2 * d_hp + 0.01 * t_donor
            rel = rel * gain * donor_amount / 0.012 * 0.012
            Y = Y * (1.0 + mask[..., 0] * rel)
            Y = np.clip(Y, 0, None)

    # ---- 2. Dodge & Burn as a small blurred exposure map -----------------------
    if db_ev > 0:
        base = gauss(Y, db_sigma)
        dmap = np.clip((Y - base) * 0.5, -db_ev, db_ev)
        dmap = gauss(dmap, db_sigma * 0.5)
        Y = Y * (2.0 ** dmap)
        y_now = 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]
        lin = lin * (Y / np.clip(y_now, EPS, None))[..., None]

    # ---- 3. OKLab colour: robust stats + partial Reinhard + variation fields --
    ref_stats = None
    if ref:
        rimg = np.asarray(Image.open(ref).convert("RGB")).astype(np.float32) / 255.0
        rlab = linear_to_oklab(srgb_to_linear(rimg))
        rm = gauss(skin_mask(rimg), 15) > 0.5
        if rm.sum() > 500:
            ref_stats = robust_stats(rlab[..., 1][rm])
    lab = linear_to_oklab(np.clip(lin, 0, None))
    L, A, B = lab[..., 0], lab[..., 1], lab[..., 2]
    m = mask[..., 0]
    sel = m > 0.5
    if sel.sum() > 1000:
        mu_s, sd_s = robust_stats(A[sel])
        if ref_stats is not None:
            # Адаптация к РЕФЕРЕНСУ (Capture One "Skin Tone -> reference"): источник - реальная
            # кожа того же человека из исходной фотографии. Раньше адаптировали к собственным
            # статистикам кадра, то есть к no-op - отсюда остаточная пятнистость тона.
            mu_r, sd_r = ref_stats
            scale = float(np.clip(sd_r / sd_s, 0.8, 1.25))
            A_new = (1 - adapt_lambda) * A + adapt_lambda * ((A - mu_s) * scale + mu_r)
            A = np.where(sel, A_new, A)
    # P3 (из экспертного ресёрча): осторожная имитация subsurface scattering.
    # Настоящий SSS из одного RGB-кадра не восстановить, но мягкий тёплый переход у
    # terminator-теней можно получить, смешав ТОЛЬКО a,b с их гауссовой копией
    # (sigma 3-6 px, mix 0.03-0.08). L и high-frequency не трогаем.
    if sss_mix > 0:
        A = A + sss_mix * m * (gauss(A, sss_sigma) - A)
        B = B + sss_mix * m * (gauss(B, sss_sigma) - B)
    A = A + warm_a * 0.01 * m
    # two independent low-frequency random fields -> natural colour variation
    rng = np.random.default_rng(seed)
    for chan in (A, B):
        n = rng.normal(0, 1, chan.shape).astype(np.float32)
        n = gauss(n, var_sigma)
        n /= (n.std() + EPS)
        chan += var_amount * n * m
    lab[..., 0], lab[..., 1], lab[..., 2] = L, A, B
    lin = oklab_to_linear(lab)

    out = np.clip(linear_to_srgb(lin), 0, 1)

    # ---- 5. grain last, global, monochrome ------------------------------------
    if grain > 0:
        lum = 0.299 * out[..., 0] + 0.587 * out[..., 1] + 0.114 * out[..., 2]
        n = rng.normal(0, 1, lum.shape).astype(np.float32)
        n = gauss(n, grain_blur)
        n /= (n.std() + EPS)
        n *= grain * (1.0 - 0.7 * lum)             # less grain in the highlights
        out = np.clip(out + n[..., None], 0, 1)

    Image.fromarray((out * 255).astype(np.uint8)).save(dst)
    return float(mask.mean())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--donor", action="append", default=[])
    ap.add_argument("--ref", default=None, help="reference photo whose skin tone we match")
    ap.add_argument("--donor-amount", type=float, default=0.012)
    ap.add_argument("--db-ev", type=float, default=0.05)
    ap.add_argument("--warm-a", type=float, default=3.0)
    ap.add_argument("--adapt", type=float, default=0.30)
    ap.add_argument("--var", type=float, default=0.0025)
    ap.add_argument("--var-sigma", type=float, default=55.0)
    ap.add_argument("--spec-break", type=float, default=0.30)
    ap.add_argument("--sss-mix", type=float, default=0.06)
    ap.add_argument("--sss-sigma", type=float, default=4.0)
    ap.add_argument("--grain", type=float, default=0.005)
    args = ap.parse_args()
    cover = retouch(args.src, args.out, donors=args.donor, ref=args.ref, donor_amount=args.donor_amount,
                    db_ev=args.db_ev, warm_a=args.warm_a, adapt_lambda=args.adapt,
                    var_amount=args.var, var_sigma=args.var_sigma,
                    spec_break=args.spec_break, sss_mix=args.sss_mix,
                    sss_sigma=args.sss_sigma, grain=args.grain)
    print(f"готово: {args.out} | кожа в маске: {cover*100:.1f}%")
