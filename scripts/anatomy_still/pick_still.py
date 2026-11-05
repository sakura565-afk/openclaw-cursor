"""Pick the anatomy still out of a rendered clip.

The action takes time: in the approved kitchen clip the frame 0 still shows the
apron down, and the anatomy only appears around frame 108. So a still has to
be chosen from the clip, not taken from its first frame.

The frame is scored by the same validator the pipeline uses, and the crop box
comes from the pose mask, so the score answers "is the anatomy visible in the
region the pose mask points at".

Usage:
  python scripts/anatomy_still/pick_still.py --clip clip.mp4 --mask mask.png \
      --out still.png [--candidates 0,60,84,96,108,120,144,168]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.anatomy_still.metrics import validate_anatomy  # noqa: E402


def extract(clip: str, index: int, out: Path) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", clip,
                    "-vf", f"select=eq(n\\,{index})", "-frames:v", "1", str(out)],
                   capture_output=True, timeout=600)
    if not out.exists():
        raise RuntimeError(f"could not extract frame {index}")
    return out


def load_rgb(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path),
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, timeout=300).stdout
    w, h = _dims(path)
    return np.frombuffer(raw, dtype=np.uint8)[:w * h * 3].reshape(h, w, 3)


def load_mask(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path),
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, timeout=300).stdout
    w, h = _dims(path)
    return np.frombuffer(raw, dtype=np.uint8)[:w * h].reshape(h, w)


def _dims(path: Path) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                       timeout=120).stdout.strip()
    w, h = (int(x) for x in r.split(",")[:2])
    return w, h


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", required=True)
    ap.add_argument("--mask", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--candidates", default="",
                    help="comma separated frame indices; empty = scan every frame")
    ap.add_argument("--step", type=int, default=4, help="scan step")
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()

    clip = Path(a.clip)
    mask = load_mask(Path(a.mask))
    work = Path(a.work_dir) if a.work_dir else Path(a.out).parent / "_pick"
    work.mkdir(parents=True, exist_ok=True)

    if a.candidates:
        indices = [int(x) for x in a.candidates.split(",") if x.strip()]
    else:
        total = int(subprocess.run(
            ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
             "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(clip)],
            capture_output=True, text=True, timeout=600).stdout.strip() or 0)
        indices = list(range(0, total, a.step))

    rows = []
    for idx in indices:
        frame = extract(str(clip), idx, work / f"f{idx:05d}.png")
        image = load_rgb(frame)
        # the mask may come from a differently sized source photo
        if mask.shape[:2] != image.shape[:2]:
            import cv2
            scaled = cv2.resize(mask, (image.shape[1], image.shape[0]),
                                interpolation=cv2.INTER_NEAREST)
        else:
            scaled = mask
        m = validate_anatomy(image, scaled)
        rows.append({"frame": idx, "visibility": m.visibility,
                     "texture_score": m.texture_score, "skin_fraction": m.skin_fraction,
                     "cloth_score": m.cloth_score, "passed": m.passed})
        print(f"  frame {idx:4d}: visibility={m.visibility:.3f} texture={m.texture_score:.3f} "
              f"skin_fraction={m.skin_fraction:.3f} cloth={m.cloth_score:.3f} passed={m.passed}")

    if not rows:
        print("no frames scanned", file=sys.stderr)
        return 2
    best = max(rows, key=lambda r: r["visibility"])
    src = work / f"f{best['frame']:05d}.png"
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, out)
    print(f"\n  best frame: {best['frame']} visibility={best['visibility']:.3f}")
    print(f"  still: {out}")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps({"best": best, "frames": rows},
                                               ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
