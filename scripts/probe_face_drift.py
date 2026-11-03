# -*- coding: utf-8 -*-
"""How many frames of a clip still hold the same face?

Two measurements per clip:

  * ``face_drift`` - mean |RGB difference| inside the face ROI between the
    first frame and every later frame, plus the same measure AFTER the
    face_lock (ReActor) pass. A low drift means the model kept the identity.
  * ``face_lock_gain`` - how much the ReActor pass reduced that drift.

The ROI is given in normalised coordinates so the same anatomical region is
used whatever the render resolution is (704x1056 for H3, 736x1120 for
FreeVideo). Frames are extracted with ffmpeg, so the numbers are measured on
the pixels the video file actually carries.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

# normalised face ROI, measured on the H3 704x1056 frame (200,180,440,400)
FACE_NORM = (200 / 704, 180 / 1056, 440 / 704, 400 / 1056)


def extract(video: Path, work: Path, max_frames: int) -> list[Path]:
    work.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video),
           "-fps_mode", "passthrough", "-start_number", "0"]
    if max_frames:
        cmd += ["-frames:v", str(max_frames)]
    cmd.append(str(work / "f_%05d.png"))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[:300]}")
    return sorted(work.glob("f_*.png"))


def face_box(size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    return (int(FACE_NORM[0] * width), int(FACE_NORM[1] * height),
            int(FACE_NORM[2] * width), int(FACE_NORM[3] * height))


def drift_series(video: Path, max_frames: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="fd_") as tmp:
        files = extract(video, Path(tmp), max_frames)
        if len(files) < 2:
            raise RuntimeError("need at least two frames")
        first = np.asarray(Image.open(files[0]).convert("RGB"), dtype=np.float32)
        x1, y1, x2, y2 = face_box((first.shape[1], first.shape[0]))
        reference = first[y1:y2, x1:x2]
        diffs: list[float] = []
        for path in files:
            frame = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
            diffs.append(float(np.abs(frame[y1:y2, x1:x2] - reference).mean()))
    values = np.array(diffs)
    return {
        "frames": len(values),
        "roi": [x1, y1, x2, y2],
        "face_drift_mean": round(float(values[1:].mean()), 4),
        "face_drift_max": round(float(values[1:].max()), 4),
        "face_drift_last": round(float(values[-1]), 4),
        # a frame "holds" the face while its drift stays under 8/255
        "frames_holding_face": int((values[1:] <= 8.0).sum()),
        "drift_series": [round(float(v), 3) for v in values],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--locked", type=Path, default=None,
                    help="the face_lock output of the same clip")
    ap.add_argument("--max-frames", type=int, default=40)
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()
    result = {"video": str(args.video), "raw": drift_series(args.video, args.max_frames)}
    if args.locked:
        result["face_locked"] = drift_series(args.locked, args.max_frames)
        raw = result["raw"]["face_drift_mean"]
        locked = result["face_locked"]["face_drift_mean"]
        result["face_lock_gain"] = round(1.0 - locked / max(raw, 1e-6), 4)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
