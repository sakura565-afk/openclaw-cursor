#!/usr/bin/env python
"""Evaluate the detail candidates against the raw FreeVideo render.

The candidates are the ones that are supposed to raise the skin-texture
ceiling without boiling: Tessera (tiled refinement inside one temporal
diffusion), the LMS sharpness LoRA on an aligned guide, and fl2va against
ref2va for identity.

Methodology, from what we measured:

- TEXTURE (pore energy) compares across anything. The seed-to-seed noise floor
  is about 1%, so that is the bar a real effect has to clear.
- FLICKER (RGB delta, HF delta, face drift) compares only between renders of
  the SAME MOTION at the same frame rate. A different seed moves the subject
  differently and inflates these numbers by hundreds of percent, and a 48 fps
  clip halves them by construction because RIFE interpolates smoothly.

So every candidate is measured against the raw reference it came from, and
any candidate that is not a re-render of that exact motion is reported as
"texture only" rather than being given a flicker verdict it cannot support.

    python scripts/eval_detail_candidates.py --baseline raw.mp4 \
        --candidate tessera=out.mp4 --candidate lms=out.mp4
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_video_texture import canonical_sizes, extract, measure  # type: ignore

NOISE_FLOOR_PCT = 1.0


def video_info(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=width,height,nb_read_frames,r_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=900)
    data = json.loads(out.stdout or "{}")
    stream = (data.get("streams") or [{}])[0]
    rate = stream.get("r_frame_rate") or "24/1"
    fps = float(rate.split("/")[0]) / max(1.0, float(rate.split("/")[1]))
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "frames": int(stream.get("nb_read_frames") or 0),
        "fps": fps,
    }


def same_motion(a: Path, b: Path) -> bool:
    """Same frame rate and a matching frame count - a proxy for same motion."""
    ia, ib = video_info(a), video_info(b)
    if abs(ia["fps"] - ib["fps"]) > 0.01:
        return False
    if not ia["frames"] or not ib["frames"]:
        return False
    ratio = ia["frames"] / ib["frames"]
    return 0.9 <= ratio <= 1.1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--candidate", action="append", default=[],
                    help="name=path, repeatable")
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--work", default="")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    base = Path(args.baseline)
    if not base.exists():
        print("baseline not found:", base, file=sys.stderr)
        return 2
    work = Path(args.work) if args.work else Path(
        os.environ.get("HERMES_SCRATCH_DIR",
                       str(Path.home() / "AppData/Local/hermes/cache/scratch"))) / "eval_frames"

    base_frames = extract(str(base), work / "baseline", args.frames)
    canonical = canonical_sizes(base_frames)
    base_m = measure(base_frames, canonical=canonical)
    info = video_info(base)
    print(f"baseline {base.name}: {info['width']}x{info['height']} {info['fps']:.3f} fps "
          f"{info['frames']} frames")
    print(f"  поры {base_m['pore']:.4e} | RGB {base_m['rgb_delta']:.4f} | "
          f"HF {base_m['hf_delta']:.4f} | лицо {base_m['face_drift']:.3f}")
    print()

    report = {"baseline": {"path": str(base), **info, **base_m}, "candidates": {}}
    for spec in args.candidate:
        if "=" not in spec:
            print("bad --candidate, expected name=path:", spec, file=sys.stderr)
            return 2
        name, raw = spec.split("=", 1)
        cand = Path(raw)
        if not cand.exists():
            print(f"  {name}: НЕТ ФАЙЛА ({cand})")
            continue
        frames = extract(str(cand), work / name, args.frames)
        if not frames:
            print(f"  {name}: нет кадров")
            continue
        m = measure(frames, canonical=canonical)
        dp = (m["pore"] / base_m["pore"] - 1) * 100
        comparable = same_motion(base, cand)
        report["candidates"][name] = {
            "path": str(cand), **video_info(cand), **m,
            "pore_delta_pct": dp,
            "significant": abs(dp) > NOISE_FLOOR_PCT,
            "flicker_comparable": comparable,
        }
        verdict = "ЗНАЧИМО" if abs(dp) > NOISE_FLOOR_PCT else "в шуме (<1%)"
        line = (f"  {name:16s} поры {m['pore']:.4e} ({dp:+6.1f}%) {verdict:14s} | "
                f"лапласиан {m['laplacian']:6.3f}")
        if comparable:
            line += (f" | RGB {(m['rgb_delta']/base_m['rgb_delta']-1)*100:+6.1f}%"
                     f" | HF {(m['hf_delta']/base_m['hf_delta']-1)*100:+6.1f}%"
                     f" | лицо {(m['face_drift']/base_m['face_drift']-1)*100:+6.1f}%")
        else:
            line += " | мерцание: НЕ СРАВНИМО (другая моторика/частота)"
        print(line)

    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print("\njson:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
