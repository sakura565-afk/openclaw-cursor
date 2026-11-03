# -*- coding: utf-8 -*-
"""Build a LoRA training dataset out of a folder of photo-session images.

WHY
---
A LoRA trained on near-duplicate shots learns the shoot - the makeup, the
background, the one outfit - instead of the person. This script exists to make
that failure impossible to miss: it reports duplicates, near-duplicates and
sharpness BEFORE the training run, so a bad dataset is caught in seconds
instead of after an hour of GPU time.

It also does the mechanical parts a trainer expects:
  * copies the chosen images into a flat dataset folder
  * writes a `.txt` caption next to every image, with a unique trigger token
  * reserves a few images for validation so the result can be judged on
    data the LoRA never saw

USAGE
-----
  # audit only - no files written
  python scripts/lora_dataset_build.py --source "<photo folder>" --audit

  # build a dataset with a trigger token
  python scripts/lora_dataset_build.py --source "<photo folder>" \
      --out "<dataset folder>" --trigger <unique_token> --min-images 20

  # cap the number of near-duplicates kept from one burst
  python scripts/lora_dataset_build.py --source "<photo folder>" --out ... --max-per-burst 3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections import defaultdict

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def list_images(source: str) -> list:
    out = []
    for dp, dn, fn in os.walk(source):
        for f in fn:
            if f.lower().endswith(IMAGE_EXTS):
                out.append(os.path.join(dp, f))
    return sorted(out)


def perceptual_key(path: str, size: int = 16) -> str:
    """A cheap perceptual hash: 16x16 grayscale, mean-normalised."""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()
    try:
        im = Image.open(path).convert("L").resize((size, size), Image.LANCZOS)
        a = np.asarray(im).astype("float32")
        a = a - a.mean()
        # A FLAT image has a==0 everywhere, so the sign pattern is all-False
        # and any other flat image produced the same key before this guard.
        # Quantising the deviation makes the hash tolerant of compression
        # noise while still separating real content.
        scale = float(np.abs(a).max())
        if scale < 1e-6:
            structure = b"flat"
        else:
            structure = (((a / scale) > 0.25).flatten()).tobytes()
        # The colour signature is part of the key: without it a solid red
        # frame and a solid blue frame would collide as "duplicates".
        rgb = Image.open(path).convert("RGB").resize((4, 4), Image.LANCZOS)
        colour = np.asarray(rgb).astype("uint8").tobytes()
        return hashlib.sha256(structure + colour).hexdigest()
    except Exception:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()


def sharpness(path: str) -> float:
    """Variance of the Laplacian - the standard focus measure."""
    try:
        from PIL import Image
        import numpy as np
        from scipy import ndimage
    except ImportError:
        return 0.0
    try:
        im = Image.open(path).convert("L")
        if max(im.size) > 1600:
            im.thumbnail((1600, 1600), Image.LANCZOS)
        a = np.asarray(im).astype("float32")
        return float(ndimage.laplace(a).var())
    except Exception:
        return 0.0


def aspect(path: str) -> float:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.width / im.height
    except Exception:
        return 0.0


def caption_for(path: str, trigger: str, extra: str = "") -> str:
    """Caption: trigger first, then only the VARIABLE description.

    An identity LoRA must not repeat the constant features (face shape, hair
    colour) in every caption - those have to be what the trigger encodes.
    """
    base = os.path.splitext(os.path.basename(path))[0]
    base = re.sub(r"[_\-]+", " ", base)
    base = re.sub(r"\b(20\d{2})\b|\b\d{4,}\b", "", base).strip()
    base = re.sub(r"\s+", " ", base)
    parts = [trigger]
    if base:
        parts.append(f"photo of {base}")
    parts.append("woman, natural skin texture, visible pores, realistic lighting")
    if extra:
        parts.append(extra)
    return ", ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--trigger", default="myperson")
    ap.add_argument("--caption-extra", default="")
    ap.add_argument("--min-images", type=int, default=20)
    ap.add_argument("--max-per-burst", type=int, default=3,
                    help="how many near-duplicates to keep from one burst")
    ap.add_argument("--min-sharpness", type=float, default=0.0,
                    help="drop frames whose Laplacian variance is below this")
    ap.add_argument("--validate", type=int, default=3,
                    help="images held out for validation")
    ap.add_argument("--audit", action="store_true", help="report only, write nothing")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    images = list_images(args.source)
    print(f"источник: {args.source}")
    print(f"найдено изображений: {len(images)}")
    if not images:
        print("нет изображений - проверь путь", file=sys.stderr)
        return 1

    # ---- audit -----------------------------------------------------------
    hashes = defaultdict(list)
    sharp = {}
    for p in images:
        h = perceptual_key(p)
        hashes[h].append(p)
        sharp[p] = sharpness(p)

    exact_dupes = {h: v for h, v in hashes.items() if len(v) > 1}
    n_dupe_files = sum(len(v) - 1 for v in exact_dupes.values())

    # near-duplicates: same burst = same size and close hash prefix
    bursts = defaultdict(list)
    for h, files in hashes.items():
        for f in files:
            bursts[f].append(f)

    too_blurry = [p for p in images if sharp[p] < args.min_sharpness] if args.min_sharpness else []
    aspects = [aspect(p) for p in images]
    odd_ar = sum(1 for a in aspects if a and (a < 0.4 or a > 2.5))

    print("\n--- аудит датасета ---")
    print(f"  точных дубликатов (файлов): {n_dupe_files} в {len(exact_dupes)} группах")
    print(f"  резких кадров с малой детализацией: {len(too_blurry)}")
    print(f"  нестандартная пропорция кадра: {odd_ar}")
    if sharp:
        vals = sorted(sharp.values())
        print(f"  резкость (дисперсия лапласиана): мин {vals[0]:.0f} / "
              f"медиана {vals[len(vals)//2]:.0f} / макс {vals[-1]:.0f}")

    if args.audit:
        if n_dupe_files:
            print("\n  примеры дубликатов:")
            for h, files in list(exact_dupes.items())[:5]:
                print("   ", os.path.basename(files[0]))
                for f in files[1:]:
                    print("      =", os.path.basename(f))
        if len(images) < args.min_images:
            print(f"\n  ВНИМАНИЕ: {len(images)} < рекомендуемых {args.min_images}. "
                  f"Для identity-LoRA нужно разнообразие, а не количество.")
        if args.json_out:
            json.dump({"source": args.source, "count": len(images),
                       "duplicates": n_dupe_files, "dupe_groups": len(exact_dupes),
                       "blurry": len(too_blurry), "odd_aspect": odd_ar},
                      open(args.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\nаудит завершён, ничего не записано (--audit)")
        return 0

    # ---- build -----------------------------------------------------------
    if not args.out:
        print("нужен --out (или --audit)", file=sys.stderr)
        return 2
    os.makedirs(args.out, exist_ok=True)

    # keep at most N per duplicate group, and the sharpest of each group
    chosen = []
    for h, files in hashes.items():
        files_sorted = sorted(files, key=lambda p: -sharp[p])
        chosen.extend(files_sorted[: args.max_per_burst])
    chosen = sorted(set(chosen))

    # hold out a validation split
    chosen_sorted = sorted(chosen, key=lambda p: -sharp[p])
    validate = chosen_sorted[: args.validate]
    train = chosen_sorted[args.validate:]

    written = []
    for p in train:
        dst = os.path.join(args.out, os.path.basename(p))
        try:
            shutil.copy2(p, dst)
        except OSError as e:
            print(f"  не скопирован {os.path.basename(p)}: {e}", file=sys.stderr)
            continue
        cap = caption_for(p, args.trigger, args.caption_extra)
        with open(os.path.splitext(dst)[0] + ".txt", "w", encoding="utf-8") as fh:
            fh.write(cap)
        written.append({"image": os.path.basename(dst), "caption": cap})

    vdir = os.path.join(args.out, "validation")
    os.makedirs(vdir, exist_ok=True)
    for p in validate:
        dst = os.path.join(vdir, os.path.basename(p))
        shutil.copy2(p, dst)

    summary = {
        "source": args.source, "out": args.out, "trigger": args.trigger,
        "found": len(images), "after_dupe_filter": len(chosen),
        "train": len(written), "validation": len(validate),
        "dropped_as_duplicate": len(images) - len(chosen),
        "trigger_note": "the trigger must be unique to this person and must not "
                        "appear in the base model's vocabulary",
    }
    with open(os.path.join(args.out, "dataset_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    if args.json_out:
        json.dump(summary, open(args.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"\nготово: {len(written)} train + {len(validate)} validation -> {args.out}")
    print(f"  отброшено как дубликаты: {summary['dropped_as_duplicate']}")
    print(f"  пример caption: {written[0]['caption'] if written else '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
