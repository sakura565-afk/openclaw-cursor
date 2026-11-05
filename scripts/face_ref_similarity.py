"""Face similarity to a REFERENCE PHOTO, per frame of a video.

`probe_face_drift.py` measures pixel drift of the face ROI against frame 0, so
a clip can hold a drifting face perfectly and still score well. That is the
wrong question when the complaint is "the face is not the reference". This
script answers the right one: how close is the face in frame N to the face in
the reference photo?

Method: InsightFace (already installed in the ComfyUI python) detects the
largest face in both the reference and each frame, crops it with the same
5-point alignment, and compares the ArcFace embeddings by cosine similarity.
Face detection failures are reported, never silently skipped - a clip where
the face cannot be found is not a clip that "passed".

Usage:
  python scripts/face_ref_similarity.py --video clip.mp4 --reference photo.jpg
  python scripts/face_ref_similarity.py --video clip.mp4 --reference photo.jpg \
      --json-out out.json --max-frames 175
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np


def _frames(video: Path, tmp: Path, max_frames: int) -> list[Path]:
    tmp.mkdir(parents=True, exist_ok=True)
    pattern = tmp / "f%05d.png"
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-fps_mode", "passthrough"]
    if max_frames:
        cmd += ["-frames:v", str(max_frames)]
    cmd += [str(pattern)]
    subprocess.run(cmd, check=True, capture_output=True)
    return sorted(tmp.glob("f*.png"))


def _embedder(app: Any):
    from insightface.app import FaceAnalysis

    fa = FaceAnalysis(name="buffalo_l", root=str(Path.home() / ".insightface"))
    fa.prepare(ctx_id=0, det_size=(640, 640))
    return fa


def _best_face(fa: Any, path: Path) -> tuple[np.ndarray | None, int]:
    img = _read_rgb(path)
    faces = fa.get(img)
    if not faces:
        return None, 0
    # largest detected face
    faces.sort(key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)
    return faces[0].normed_embedding, len(faces)


def _read_rgb(path: Path) -> np.ndarray:
    import cv2

    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise RuntimeError(f"не читается {path}")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--json-out", type=Path, default=None)
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--sample-every", type=int, default=1,
                    help="measure every Nth frame (1 = every frame)")
    a = ap.parse_args()

    if not a.video.exists():
        raise SystemExit(f"нет видео: {a.video}")
    if not a.reference.exists():
        raise SystemExit(f"нет референса: {a.reference}")

    try:
        import insightface  # noqa: F401
    except ImportError:
        raise SystemExit("нужен insightface (он есть в python ComfyUI)")

    fa = _embedder(None)
    ref_emb, n_ref = _best_face(fa, a.reference)
    if ref_emb is None:
        raise SystemExit("в референсе не найдено лицо")
    print(f"референс: лицо найдено ({n_ref} в кадре)", flush=True)

    with tempfile.TemporaryDirectory(prefix="facesim_") as td:
        frames = _frames(a.video, Path(td), a.max_frames)
        sims: list[float] = []
        detected = 0
        missed: list[int] = []
        for i, f in enumerate(frames):
            if a.sample_every > 1 and i % a.sample_every:
                continue
            emb, n = _best_face(fa, f)
            if emb is None:
                missed.append(i)
                continue
            detected += 1
            sims.append(float(np.dot(ref_emb, emb)))
            if detected % 25 == 0:
                print(f"  кадр {i}: sim={sims[-1]:.3f} (распознано {detected})", flush=True)

    measured = len(sims)
    result = {
        "video": str(a.video),
        "reference": str(a.reference),
        "frames_total": len(frames),
        "frames_measured": measured,
        "frames_face_not_detected": len(missed),
        "frames_missed_idx": missed[:40],
        "sim_mean": round(float(np.mean(sims)), 4) if sims else None,
        "sim_min": round(float(np.min(sims)), 4) if sims else None,
        "sim_max": round(float(np.max(sims)), 4) if sims else None,
        "sim_first": round(sims[0], 4) if sims else None,
        "sim_last": round(sims[-1], 4) if sims else None,
        "sim_series": [round(s, 4) for s in sims],
        "threshold": 0.45,
        "verdict": None,
    }
    if sims:
        m = result["sim_mean"]
        result["verdict"] = ("reference" if m >= 0.55 else
                             "partially_reference" if m >= 0.45 else
                             "NOT_reference")
    out = json.dumps(result, ensure_ascii=False, indent=2)
    print(out[:2000])
    if a.json_out:
        a.json_out.parent.mkdir(parents=True, exist_ok=True)
        a.json_out.write_text(out, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
