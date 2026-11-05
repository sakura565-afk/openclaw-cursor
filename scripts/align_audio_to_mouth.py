"""Find the audio window that lines up with the mouth, then mux it.

The first version of this tool measured a "lag" and shifted the audio by it.
That was wrong: the sign convention was easy to get backwards, and shifting
the window the wrong way made the fit worse. This version does not reason
about signs at all - it scans the audio window over a range and keeps the
position that maximises the correlation with the mouth series.

That is also the physically right model. LatentSync's mouth is driven by the
audio window it was given, so the mouth ends up aligned with that same window.
The window delivered alongside it may have been a different one, and the
mismatch is what the viewer hears as "the lips are not on the words".

Measured on the boho clip: the best window was 37.30 s while 38.0 s had been
delivered - a 700 ms offset, which is why the lips looked close but never
quite landed on the vocal.

Usage:
  python scripts/align_audio_to_mouth.py --clip clip.mp4 --track song.flac \
      --vocal vocals.wav --search 36.5 39.5 --out clip_aligned.mp4
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lipsync_vocal_score import (  # noqa: E402
    corr, decode, envelope, mouth_openness,
)

HOP = 0.04


def scan_window(mouth: np.ndarray, env_full: np.ndarray, sr: int,
                lo: float, hi: float, duration: float, fps: int) -> list[tuple[float, float]]:
    """Every (correlation, window_start) pair over the searched range."""
    idx = (np.arange(len(mouth)) / float(fps)) / HOP
    nenv = int(duration / HOP) + 1
    out = []
    for W in np.arange(lo, hi + 1e-9, HOP):
        i0 = int(round(W / HOP))
        seg = env_full[i0:i0 + nenv]
        if len(seg) < nenv:
            continue
        a = np.interp(idx, np.arange(len(seg)), seg)
        out.append((corr(a, mouth), float(W)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", type=Path, required=True)
    ap.add_argument("--track", type=Path, required=True)
    ap.add_argument("--vocal", type=Path, required=True,
                    help="vocals-only stem; the reference the mouth must follow")
    ap.add_argument("--search", type=float, nargs=2, default=(36.5, 39.5),
                    metavar=("LO", "HI"), help="window start range to scan")
    ap.add_argument("--duration", type=float, default=7.3)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--mouth-box", default="250,390,140,90")
    ap.add_argument("--out", type=Path, default=None,
                    help="write the muxed clip here; dry run if omitted")
    ap.add_argument("--json-out", type=Path, default=None)
    a = ap.parse_args()

    if not a.clip.exists() or not a.track.exists() or not a.vocal.exists():
        print("нет клипа, трека или вокального стема", file=sys.stderr)
        return 2

    box = tuple(int(v) for v in a.mouth_box.split(","))
    voc, sr = decode(a.vocal)
    mix, _ = decode(a.track)
    e_voc = envelope(voc, sr)
    e_mix = envelope(mix, sr)

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        mouth = mouth_openness(a.clip, box, a.fps, work / "mouth")
        if mouth.size == 0:
            print("не удалось прочитать кадры", file=sys.stderr)
            return 4

        rows_v = scan_window(mouth, e_voc, sr, a.search[0], a.search[1],
                             a.duration, a.fps)
        rows_m = scan_window(mouth, e_mix, sr, a.search[0], a.search[1],
                             a.duration, a.fps)
        c_voc, W_voc = max(rows_v)
        c_mix, W_mix = max(rows_m)

        # prefer the vocal window, but only when it is also a good window for
        # the mix - the two must not disagree, the stem is time aligned
        W = W_voc
        if abs(W_voc - W_mix) > 0.15:
            W = W_mix

        res = {
            "clip": str(a.clip),
            "best_window_vocal": {"start": round(W_voc, 3), "corr": round(c_voc, 4)},
            "best_window_mix": {"start": round(W_mix, 3), "corr": round(c_mix, 4)},
            "chosen_start": round(W, 3),
            "frames": int(mouth.size),
            "fps": a.fps,
            "mouth_amplitude": round(float(mouth.max() - mouth.min()), 4),
        }
        print(json.dumps(res, ensure_ascii=False, indent=2))
        if a.json_out:
            a.json_out.write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        if a.out is None:
            return 0

        aud = work / "window.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{W:.4f}",
                        "-t", f"{a.duration:.4f}", "-i", str(a.track),
                        "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(aud)],
                       capture_output=True, timeout=600)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(a.clip), "-i", str(aud),
                        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                        "-shortest", str(a.out)], capture_output=True, timeout=900)
        if not a.out.exists():
            print("мукс не удался", file=sys.stderr)
            return 3
        print(f"  готово: {a.out} ({a.out.stat().st_size} байт, окно с {W:.2f} с)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
