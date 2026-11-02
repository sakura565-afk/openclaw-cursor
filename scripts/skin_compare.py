"""Skin comparison sheet: N frames -> 1:1 crop panels + optional vision verdict.

Usage:
  python skin_compare.py --files a.png b.png c.png --labels "A" "B" "C" --out sheet.png
  python skin_compare.py --files ... --region thigh
Region coordinates are given for a 1088x1620 reference frame and scaled per image.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

SCRATCH = r"C:\Users\user\AppData\Local\hermes\cache\scratch"

# region -> (x, y, w, h) in 1088x1620 reference coordinates
REGIONS = {
    "thigh": (250, 900, 360, 420),
    "torso": (360, 520, 360, 420),
    "face": (300, 120, 360, 360),
    "chest": (250, 380, 360, 360),
}


def crop_region(src: str, region: str, out: str) -> tuple[int, int]:
    x, y, w, h = REGIONS[region]
    im = Image.open(src)
    iw, ih = im.size
    k = iw / 1088.0
    x, y, w, h = int(x * k), int(y * k), int(w * k), int(h * k)
    x = max(0, min(x, iw - 1))
    y = max(0, min(y, ih - 1))
    w = min(w, iw - x)
    h = min(h, ih - y)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src,
                    "-vf", f"crop={w}:{h}:{x}:{y}", out],
                   capture_output=True, timeout=120)
    return w, h


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--labels", nargs="*", default=None)
    ap.add_argument("--region", default="thigh", choices=sorted(REGIONS))
    ap.add_argument("--out", default=os.path.join(SCRATCH, "skin_sheet.png"))
    ap.add_argument("--cell", type=int, default=340, help="cell width in px")
    args = ap.parse_args()

    labels = args.labels or [os.path.basename(f).replace(".png", "") for f in args.files]
    crops = []
    for i, f in enumerate(args.files):
        if not os.path.exists(f):
            print("НЕТ ФАЙЛА:", f)
            continue
        out = os.path.join(SCRATCH, f"_sc_{i}.png")
        crop_region(f, args.region, out)
        crops.append((out, labels[i] if i < len(labels) else os.path.basename(f)))

    if not crops:
        print("нечего сравнивать")
        return 1

    imgs = [Image.open(p).convert("RGB") for p, _ in crops]
    cw = args.cell
    ch = int(round(cw * imgs[0].size[1] / imgs[0].size[0]))
    head = 30
    gap = 8
    canvas = Image.new("RGB", (cw * len(imgs) + gap * (len(imgs) - 1), ch + head), (255, 255, 255))
    try:
        from PIL import ImageFont
        f = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 18)
    except Exception:
        f = None
    d = ImageDraw.Draw(canvas)
    for i, (im, (p, lab)) in enumerate(zip(imgs, crops)):
        canvas.paste(im.resize((cw, ch), Image.LANCZOS), (i * (cw + gap), head))
        d.text((i * (cw + gap) + 3, 5), lab, fill=(0, 0, 0), font=f)
    canvas.save(args.out)
    print("готово:", args.out, os.path.getsize(args.out) // 1024, "КБ",
          "| панелей:", len(imgs), "| регион:", args.region)
    return 0


if __name__ == "__main__":
    sys.exit(main())
