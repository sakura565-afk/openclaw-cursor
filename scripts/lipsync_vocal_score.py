"""Score how well a lip-synced clip follows the vocal, not the mix.

`lipsync_score.py` correlates the mouth openness with the audio envelope of
whatever file you hand it. On a mixed track that number is mostly measuring
the guitar: the low end carries almost all of the energy, so the mouth looks
like it follows the beat while the words are not being followed at all.

This scorer separates the track first (demucs) and correlates the mouth with
the **vocals-only** stem, so the number answers the question that matters:
do the lips match the singing?

It reports both numbers so the mix-driven and vocal-driven scores can be
compared side by side.

Usage:
  python scripts/lipsync_vocal_score.py --video clip.mp4 --track song.flac \
      --vocal vocals.wav --start 38.0 --duration 7.3
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import wave
from array import array
from pathlib import Path

import numpy as np

MouthBox = tuple[int, int, int, int]


def decode(path: Path, ss: float | None = None, dur: float | None = None,
           rate: int = 16000) -> tuple[np.ndarray, int]:
    """Decode any audio file to mono float32 via ffmpeg."""
    cmd = ["ffmpeg", "-v", "error"]
    if ss is not None:
        cmd += ["-ss", f"{ss:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", str(path), "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True, timeout=600).stdout
    if not raw:
        return np.zeros(0, dtype=np.float32), rate
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0, rate


def envelope(x: np.ndarray, sr: int, hop_s: float = 0.04) -> np.ndarray:
    """RMS envelope, normalised to 0..1."""
    if x.size == 0:
        return np.zeros(0, dtype=np.float32)
    hop = max(1, int(sr * hop_s))
    usable = len(x) - hop
    if usable <= 0:
        return np.array([float(np.sqrt((x ** 2).mean()))], dtype=np.float32)
    e = np.array([np.sqrt((x[i:i + hop] ** 2).mean()) for i in range(0, usable, hop)],
                 dtype=np.float32)
    return e / (e.max() or 1.0)


def mouth_openness(video: Path, box: MouthBox, fps: int,
                   workdir: Path) -> np.ndarray:
    """Per-frame fraction of dark pixels in the mouth crop."""
    x, y, w, h = box
    workdir.mkdir(parents=True, exist_ok=True)
    for f in workdir.glob("*.png"):
        f.unlink()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video),
                    "-vf", f"crop={w}:{h}:{x}:{y}", "-fps_mode", "passthrough",
                    str(workdir / "m%05d.png")], capture_output=True, timeout=1800)
    out = []
    for f in sorted(workdir.glob("*.png")):
        rr = subprocess.run(["ffmpeg", "-v", "error", "-i", str(f), "-vf", "format=gray",
                             "-f", "rawvideo", "-"], capture_output=True, timeout=120)
        buf = np.frombuffer(rr.stdout, dtype=np.uint8)
        out.append(float((buf < 70).mean()) if buf.size else 0.0)
    return np.array(out, dtype=np.float32)


def corr(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    if n < 8:
        return 0.0
    a, b = a[:n], b[:n]
    if a.std() < 1e-9 or b.std() < 1e-9:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def best_lag(a: np.ndarray, b: np.ndarray, span: int = 30) -> tuple[float, int]:
    """Best correlation of `a` (audio) against `b` (mouth), shifted by +-span.

    Sign convention: the returned lag is the shift applied to the MOUTH
    series. A POSITIVE lag means the mouth trails the audio - the shape at
    frame j belongs to a moment that comes later in the track. A NEGATIVE lag
    means the mouth is ahead of the audio.
    """
    best = (-2.0, 0)
    for lag in range(-span, span + 1):
        if lag < 0:
            c = corr(a[-lag:], b[:len(b) + lag])
        elif lag > 0:
            c = corr(a[:len(a) - lag], b[lag:])
        else:
            c = corr(a, b)
        if c > best[0]:
            best = (c, lag)
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--track", type=Path, required=True, help="full mix")
    ap.add_argument("--vocal", type=Path, default=None,
                    help="vocals-only stem; separated on the fly if absent")
    ap.add_argument("--demucs-python", default=None,
                    help="interpreter that has demucs installed")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--mouth-box", default="250,390,140,90",
                    help="x,y,w,h of the mouth crop in the source resolution")
    ap.add_argument("--json-out", type=Path, default=None)
    a = ap.parse_args()

    if not a.video.exists() or not a.track.exists():
        print("нет видео или трека", file=sys.stderr)
        return 2

    box = tuple(int(v) for v in a.mouth_box.split(","))
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)

        vocal = a.vocal
        if vocal is None:
            if not a.demucs_python:
                print("нужен --vocal или --demucs-python", file=sys.stderr)
                return 2
            outdir = work / "demucs"
            cmd = [a.demucs_python, "-m", "demucs", "--two-stems=vocals",
                   "-n", "htdemucs", "-o", str(outdir), str(a.track)]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
            found = sorted(outdir.rglob("vocals.wav"))
            if r.returncode != 0 or not found:
                print("demucs не отделил вокал", file=sys.stderr)
                print(r.stderr[-500:], file=sys.stderr)
                return 3
            vocal = found[0]

        mix, sr = decode(a.track, a.start, a.duration)
        voc, _ = decode(vocal)
        # the stem is aligned with the mix; take the same window out of it
        voc_win, _ = decode(vocal, a.start, a.duration)

        mouth = mouth_openness(a.video, box, a.fps, work / "mouth")
        if mouth.size == 0:
            print("не удалось прочитать кадры", file=sys.stderr)
            return 4

        emix = envelope(mix, sr)
        evoc = envelope(voc_win, sr)
        idx = (np.arange(len(mouth)) / float(a.fps)) / 0.04
        e_mix = np.interp(idx, np.arange(len(emix)), emix)
        e_voc = np.interp(idx, np.arange(len(evoc)), evoc)

        c_mix, l_mix = best_lag(e_mix, mouth)
        c_voc, l_voc = best_lag(e_voc, mouth)

        res = {
            "video": str(a.video),
            "track": str(a.track),
            "vocal_stem": str(vocal),
            "frames": int(mouth.size),
            "fps": a.fps,
            "corr_with_mix": round(c_mix, 4),
            "corr_with_vocal": round(c_voc, 4),
            "best_lag_mix_frames": l_mix,
            "best_lag_vocal_frames": l_voc,
            "mouth_amplitude": round(float(mouth.max() - mouth.min()), 4),
            "vocal_share_of_mix_band": None,
        }
        # how much of the mix energy sits in the vocal band, for context
        try:
            N = 4096
            win = np.hanning(N)
            fr = np.fft.rfftfreq(N, 1 / sr)
            acc = {"vocal": 0.0, "rest": 0.0}
            for i in range(0, len(mix) - N, N):
                sp = np.abs(np.fft.rfft(mix[i:i + N] * win))
                acc["vocal"] += float(sp[(fr >= 1200) & (fr < 3500)].mean())
                acc["rest"] += float(sp[(fr >= 30) & (fr < 1200)].mean())
            tot = acc["vocal"] + acc["rest"]
            res["vocal_share_of_mix_band"] = round(acc["vocal"] / tot, 4) if tot else None
        except Exception:
            pass

        print(json.dumps(res, ensure_ascii=False, indent=2))
        if a.json_out:
            a.json_out.write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
