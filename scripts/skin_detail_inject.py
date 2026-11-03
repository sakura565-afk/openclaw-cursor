
"""
skin_detail_inject.py  -  temporal-stable high-frequency skin detail for AI video.

Adds pore-scale high-frequency detail to a video WITHOUT temporal crawl, by:
  1. Deriving a per-pixel "detail budget" from local luminance variance in a
     skin mask (so pores only appear where skin is, and NOT on flat wall).
  2. Generating a SINGLE high-pass noise field (fixed seed) and warping it with
     optical flow so it sticks to the moving subject (otherwise it swims).
  3. High-pass filtering so we inject only HIGH frequencies (pores), never
     low-frequency luminance blotches (which would read as dirt/flicker).
  4. Temporal low-pass on the INJECTED layer only, so the injected detail
     fades in/out smoothly and never flickers between frames.

This is the "add frequencies, don't amplify them" principle:
unsharp masks only boost frequencies that already exist; this ADDS new ones.
"""
import numpy as np


# ---------- helpers ----------
def _gaussian_kernel(sigma):
    radius = max(1, int(round(3.0 * sigma)))
    x = np.arange(-radius, radius + 1, dtype=np.float32)
    k = np.exp(-(x ** 2) / (2.0 * sigma ** 2))
    return k / k.sum()


def _gauss_blur_1d(img, sigma, axis):
    k = _gaussian_kernel(sigma)
    pad = len(k) // 2
    h, w = img.shape[:2]
    if axis == 0:  # vertical
        padded = np.pad(img, ((pad, pad), (0, 0)), mode="edge")
        out = np.zeros((h, w), dtype=np.float32)
        for i, wgt in enumerate(k):
            out += wgt * padded[i:i + h, :]
        return out
    else:  # horizontal
        padded = np.pad(img, ((0, 0), (pad, pad)), mode="edge")
        out = np.zeros((h, w), dtype=np.float32)
        for i, wgt in enumerate(k):
            out += wgt * padded[:, i:i + w]
        return out


def gauss_blur(img, sigma):
    """Separable gaussian blur on a float32 HxW image."""
    if sigma <= 0:
        return img.astype(np.float32)
    return _gauss_blur_1d(_gauss_blur_1d(img.astype(np.float32), sigma, 0), sigma, 1)


def highpass(img, sigma):
    """img - blur(img): keeps only high frequencies (pores / micro texture)."""
    return img.astype(np.float32) - gauss_blur(img, sigma)


def local_variance(img, sigma):
    """Local std-dev of luminance -> used as a 'how textured is it here' map."""
    mean = gauss_blur(img, sigma)
    mean2 = gauss_blur(img * img, sigma)
    return np.sqrt(np.maximum(mean2 - mean * mean, 0.0))


# ---------- main ----------
def inject_skin_detail(frames, skin_mask=None, strength=0.35,
                       detail_sigma=1.6, pore_sigma=0.55,
                       flow=None, seed=1234, temporal_sigma=0.0,
                       detail_floor=0.35, two_octave=True):
    """
    frames      : list of float32 HxWx3 in [0,1]
    skin_mask   : list of float32 HxW in [0,1] (optional). If None, auto-detect skin.
    strength    : overall amplitude of injected detail (0 = off)
    detail_sigma: gaussian sigma used to split low/high frequencies (px)
    pore_sigma  : gaussian sigma of the HIGH-pass shaping (smaller = finer pores)
    flow        : list of (dx, dy) float32 HxW optical flow FROM frame i TO frame i-1
                   (or None -> no warping, detail is static in screen space)
    seed        : fixed seed -> identical detail pattern every run (no temporal noise)
    temporal_sigma : if >0, temporally smooth the injected amount to avoid flicker
    detail_floor: fraction of detail kept in flat regions (0..1). 0 = pores ONLY in
                   already-textured areas, 1 = pores everywhere in the mask.

    returns list of float32 HxWx3 frames with detail injected.
    """
    n = len(frames)
    if n == 0 or strength <= 0:
        return frames

    rng = np.random.default_rng(seed)
    h, w = frames[0].shape[:2]

    # --- 1. build ONE fixed high-pass noise field (screen space) -------------
    noise = rng.standard_normal((h, w)).astype(np.float32)
    # Shape it into pore-like blobs: highpass of noise, then blur tiny.
    noise_hp = highpass(noise, pore_sigma)
    noise_hp /= (np.std(noise_hp) + 1e-8)

    # Two band-passed octaves plus sparse darker pits - the shape BCNodes uses
    # for its pore field, which reads better than a single high-passed octave.
    # BCNodes draws a DIFFERENT random field per frame from one seed, so its
    # pores crawl by construction; here the same field is warped by optical
    # flow instead, which keeps the shape and drops the crawl.
    if two_octave:
        fine = highpass(noise, pore_sigma * 0.55)
        coarse = highpass(noise, pore_sigma * 1.6)
        fine /= (np.std(fine) + 1e-8)
        coarse /= (np.std(coarse) + 1e-8)
        pits = -np.maximum(coarse - 1.25, 0.0) * 1.5
        field = 0.65 * fine + 0.35 * coarse + pits
        field /= (np.std(field) + 1e-8)
        noise_hp = field.astype(np.float32)

    # --- 2. per-frame skin mask --------------------------------------------
    if skin_mask is None:
        skin_mask = auto_skin_mask(frames)

    # --- 3. compute detail budget (where can pores live?) -------------------
    budgets = []
    for i, f in enumerate(frames):
        lum = f[..., 0] * 0.299 + f[..., 1] * 0.587 + f[..., 2] * 0.114
        var = local_variance(lum, 3.0)
        # normalize to 0..1 with a soft knee
        v = var / (var.max() + 1e-8)
        b = detail_floor + (1.0 - detail_floor) * np.clip(v * 3.0, 0.0, 1.0)
        budgets.append(b * skin_mask[i])

    # --- 4. temporal smoothing of the budget (kills flicker) ----------------
    if temporal_sigma > 0 and n > 2:
        smoothed = []
        for i in range(n):
            lo = max(0, i - 1); hi = min(n, i + 2)
            stack = np.stack(budgets[lo:hi], axis=0)
            smoothed.append(stack.mean(axis=0))
        budgets = smoothed

    # --- 5. warp the noise field with optical flow (anti-crawl) -------------
    if flow is not None:
        warped = []
        cur = noise_hp.copy()
        for i in range(n):
            if i > 0:
                dx, dy = flow[i]
                cur = warp(cur, dx, dy)
            warped.append(cur.copy())
    else:
        warped = [noise_hp.copy() for _ in range(n)]

    # --- 6. inject ------------------------------------------------------------
    out = []
    for i, f in enumerate(frames):
        # highpass of the CURRENT frame = the frequencies that are MISSING
        lum = f[..., 0] * 0.299 + f[..., 1] * 0.587 + f[..., 2] * 0.114
        base_hp = highpass(lum, detail_sigma)
        # we ADD a synthetic high-pass, scaled by budget
        add = warped[i] * budgets[i] * strength
        # apply the same delta to all 3 channels (luminance detail, keeps hue)
        delta = np.stack([add, add, add], axis=-1)
        out.append(np.clip(f + delta, 0.0, 1.0).astype(np.float32))
    return out


def warp(img, dx, dy):
    """Warp img by flow field (dx,dy) using bilinear sampling (backward map)."""
    h, w = img.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    sx = xx - dx
    sy = yy - dy
    x0 = np.floor(sx).astype(np.int32); y0 = np.floor(sy).astype(np.int32)
    x1 = np.clip(x0 + 1, 0, w - 1); y1 = np.clip(y0 + 1, 0, h - 1)
    x0 = np.clip(x0, 0, w - 1);    y0 = np.clip(y0, 0, h - 1)
    fx = (sx - x0).astype(np.float32); fy = (sy - y0).astype(np.float32)
    out = (img[y0, x0] * (1 - fx) * (1 - fy) +
           img[y0, x1] * fx * (1 - fy) +
           img[y1, x0] * (1 - fx) * fy +
           img[y1, x1] * fx * fy)
    return out.astype(np.float32)


def auto_skin_mask(frames, smooth_sigma=2.0):
    """Fallback YCrCb skin prior.

    NOTE: this is only a rough fallback. Prefer a real BiRefNet / SAM2 mask
    (BC_BiRefNetRemoveBackground in ComfyUI) - the prior alone picks up
    beige walls and backgrounds, which is exactly how pores end up in a wall.
    """
    masks = []
    for f in frames:
        r, g, b = f[..., 0], f[..., 1], f[..., 2]
        # classic YCrCb skin range (Chai & Ngan):  133<=Cr<=173, 77<=Cb<=127
        y  =  0.299 * r + 0.587 * g + 0.114 * b
        cb = -0.168736 * r - 0.331264 * g + 0.5 * b + 128.0
        cr =  0.5 * r - 0.418688 * g - 0.081312 * b + 128.0
        m = ((cr >= 133) & (cr <= 173) & (cb >= 77) & (cb <= 127) &
             (y > 40) & (r > g) & (r > b)).astype(np.float32)
        m = gauss_blur(m, smooth_sigma)
        masks.append(np.clip(m * 2.0, 0.0, 1.0))
    return masks
