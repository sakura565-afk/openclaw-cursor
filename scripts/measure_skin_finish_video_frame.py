# -*- coding: utf-8 -*-
"""Does the still-pipeline skin finish (BC pores + grade) help a VIDEO frame?

Measures the mean |laplacian| (micro-texture proxy) before and after, on the
skin region and on the background region, so we can tell real skin detail from
a global grain/sharpen that also hits the wall.
"""
import os
import subprocess
import sys

import numpy as np
from PIL import Image

SCRATCH = os.environ.get(
    "HERMES_SCRATCH_DIR",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "cache", "scratch"),
)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lap_metric(x):
    g = x.mean(axis=2)
    lap = (g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4 * g[1:-1, 1:-1])
    return float(np.abs(lap).mean())


def roi_met(x, r):
    sub = x[r]
    return lap_metric(sub)


def load(p, size=None):
    im = Image.open(p).convert("RGB")
    if size:
        im = im.resize(size, Image.LANCZOS)
    return np.asarray(im).astype(np.float32)


def main():
    src = os.path.join(SCRATCH, "ab_f0.png")
    if not os.path.exists(src):
        print("нет кадра")
        return 1
    # маска кожи - новая person-aware, со скилла
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    try:
        from skin_mask_v2 import skin_mask_v2
        use_v2 = True
    except Exception as e:
        print("skin_mask_v2 недоступен:", str(e)[:80], "- используем старую маску")
        use_v2 = False
    if not use_v2:
        from skin_detail_shade import skin_mask
        a = np.asarray(Image.open(src).convert("RGB")).astype(np.float32) / 255
        mask = (skin_mask(a) > 0.45).astype(np.uint8) * 255
    else:
        im = Image.open(src).convert("RGB")
        arr = np.asarray(im).astype(np.float32) / 255
        m = skin_mask_v2(arr)
        mask = (np.asarray(m) * 255).astype(np.uint8)
    mask_path = os.path.join(SCRATCH, "mask_abf0.png")
    Image.fromarray(mask).save(mask_path)
    print(f"маска ({'v2' if use_v2 else 'v1'}): {float((mask > 0).mean()) * 100:.1f}% кадра")

    before = load(src)
    after_path = os.path.join(SCRATCH, "BST_ab_f0_10.png")
    if os.path.exists(after_path):
        after = load(after_path)
        skin = (slice(450, 950), slice(180, 560))
        wall = (slice(30, 220), slice(30, 320))
        print(f"до  : кожа {roi_met(before, skin):6.3f} | стена {roi_met(before, wall):6.3f}")
        print(f"после: кожа {roi_met(after, skin):6.3f} | стена {roi_met(after, wall):6.3f}")
        d_skin = roi_met(after, skin) / max(roi_met(before, skin), 1e-6)
        d_wall = roi_met(after, wall) / max(roi_met(before, wall), 1e-6)
        print(f"прирост: кожа {d_skin:.2f}x | стена {d_wall:.2f}x")
        print(f"ИЗОЛЯЦИЯ (стена/кожа): {d_wall / max(d_skin, 1e-6):.2f}  (1.0 = идеально, "
              f">1.5 = текстура протекла на фон)")
    else:
        print("нет результата BC:", after_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
