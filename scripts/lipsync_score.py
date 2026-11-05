"""Measure a clip's lip-sync against its own audio track.

The mouth has to follow the WORDS, not merely move. Two numbers matter:

``openness``
    Fraction of dark pixels inside the mouth box. An open mouth shows the
    dark interior, so the signal rises when she sings and falls when she
    closes her lips. Measured per frame.

``corr``
    Pearson correlation between the audio envelope (40 ms RMS) and the
    openness signal, resampled to a common rate.

A negative corr on raw brightness means "louder -> darker -> mouth open".
This script reports the openness correlation directly, so a positive value
means the mouth tracks the music.

Usage:
  python scripts/lipsync_score.py --video clip.mp4 --audio track.wav
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np


def _audio_env(audio: Path, sr: int = 16000) -> tuple[np.ndarray, int]:
    with wave.open(str(audio), "rb") as w:
        n, ch, sw = w.getnframes(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    import array
    a = array.array("h")
    a.frombytes(raw)
    x = np.array(a, dtype=np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    hop = int(sr * 0.04)
    env = np.array([np.sqrt((x[i:i + hop] ** 2).mean()) for i in range(0, len(x) - hop, hop)])
    if env.max() > env.min():
        env = (env - env.min()) / (env.max() - env.min())
    return env, sr


def _mouth_openness(video: Path, box: tuple[int, int, int, int], tmp: Path) -> np.ndarray:
    """Dark-pixel fraction inside the mouth box for every frame."""
    tmp.mkdir(parents=True, exist_ok=True)
    x, y, w, h = box
    pattern = tmp / "m%05d.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(video),
         "-vf", f"crop={w}:{h}:{x}:{y}", "-fps_mode", "passthrough", str(pattern)],
        check=True, capture_output=True)
    frames = sorted(tmp.glob("m*.png"))
    out = []
    for f in frames:
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(f), "-vf", "format=gray",
             "-f", "rawvideo", "-"], capture_output=True, timeout=60)
        buf = np.frombuffer(r.stdout, dtype=np.uint8)
        out.append(float((buf < 60).mean()) if buf.size else 0.0)
    return np.array(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--audio", type=Path, required=True)
    ap.add_argument("--box", default="270,430,200,110",
                    help="mouth box x,y,w,h in video pixels")
    ap.add_argument("--json-out", type=Path, default=None)
    a = ap.parse_args()

    import tempfile
    x, y, w, h = (int(v) for v in a.box.split(","))
    with tempfile.TemporaryDirectory(prefix="lipsync_score_") as td:
        open_ = _mouth_openness(a.video, (x, y, w, h), Path(td))

    env, sr = _audio_env(a.audio)
    n = min(len(open_), len(env))
    if n < 8:
        raise SystemExit("слишком мало кадров для замера")
    step = max(1, len(env) // n)
    e = env[:n * step:step][:n]
    o = open_[:n]
    corr = float(np.corrcoef(e, o)[0, 1])
    rng = float(o.max() - o.min())

    result = {
        "video": str(a.video),
        "audio": str(a.audio),
        "frames_measured": n,
        "openness_min": round(float(o.min()), 4),
        "openness_max": round(float(o.max()), 4),
        "openness_range": round(rng, 4),
        "openness_std": round(float(o.std()), 4),
        "corr_audio_vs_mouth_openness": round(corr, 4),
        "mouth_alive": bool(rng > 0.15),
        "verdict": None,
    }
    if corr >= 0.30:
        result["verdict"] = "lip-sync present"
    elif corr >= 0.10:
        result["verdict"] = "partial lip-sync"
    elif corr <= -0.10:
        result["verdict"] = "inverted (lips move but against the music)"
    else:
        result["verdict"] = "no measurable lip-sync"
    out = json.dumps(result, ensure_ascii=False, indent=2)
    print(out)
    if a.json_out:
        a.json_out.write_text(out, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
