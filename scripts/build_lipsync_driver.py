"""Build the LatentSync driver track and the delivered audio from a music file.

Two problems have to be solved at once.

**The mix problem.** LatentSync derives its audio features from whatever file it
is given. Hand it the full mix and the mouth follows the loudest thing in it -
usually the guitar - so the lips do not match the words even though they move
in time with the track. Hand it a vocals-only stem and the mouth follows the
voice, which is what "the lips match the vocal" means.

**The latency problem.** The pipeline's mouth leads its input by a measured
~625 ms (the mouth at frame j matches the audio at j+15). So the driver has to
start 625 ms *earlier* than the window that ends up in the video, while the
delivered audio stays on the original timing.

Usage:
  python scripts/build_lipsync_driver.py --track song.flac --vocal vocals.wav \
      --start 38.0 --duration 7.3 --out-driver driver.wav --out-audio audio.wav
"""
from __future__ import annotations

import argparse
import os
import subprocess
import wave
from pathlib import Path

# Measured on the boho clip: the mouth at frame j matches the audio at j+15
# (25 fps). Keep it in one place so the value is never guessed.
LEAD_FRAMES = 15
DRIVER_FPS = 25


def _ff(src: Path, dst: Path, ss: float | None = None, dur: float | None = None,
        ch: int = 1, sr: int = 16000) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if ss is not None:
        cmd += ["-ss", f"{ss:.4f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.4f}"]
    cmd += ["-i", str(src), "-ac", str(ch), "-ar", str(sr), "-c:a", "pcm_s16le", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg не смог: {r.stderr[-400:]}")


def _duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", type=Path, required=True,
                    help="full mix (what the user hears)")
    ap.add_argument("--vocal", type=Path, required=True,
                    help="vocals-only stem, time-aligned with the mix")
    ap.add_argument("--start", type=float, required=True,
                    help="window start in the track, seconds")
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--out-driver", type=Path, required=True)
    ap.add_argument("--out-audio", type=Path, required=True)
    ap.add_argument("--lead", type=float, default=LEAD_FRAMES / DRIVER_FPS,
                    help="how much earlier the driver starts, seconds")
    a = ap.parse_args()

    for p in (a.track, a.vocal):
        if not p.exists():
            raise SystemExit(f"нет файла: {p}")

    # DELIVERED audio: the mix window, original timing.
    _ff(a.track, a.out_audio, ss=a.start, dur=a.duration, ch=2, sr=44100)
    got = _duration(a.out_audio)
    print(f"аудио (микс): {a.start:.3f}..{a.start + a.duration:.3f} с -> {got:.3f} с")

    # DRIVER: the vocal stem, starting `lead` earlier so that the mouth the
    # pipeline produces lands on the mix window. The length stays exactly the
    # clip length: LatentSync pads (loops) the video when the audio is longer,
    # and a loop would re-run the inference over duplicated frames.
    a.out_driver.parent.mkdir(parents=True, exist_ok=True)
    d_start = max(0.0, a.start - a.lead)
    _ff(a.vocal, a.out_driver, ss=d_start, dur=a.duration, ch=1, sr=16000)
    got_d = _duration(a.out_driver)
    print(f"драйвер (вокал): {d_start:.3f}..{d_start + a.duration:.3f} с -> {got_d:.3f} с "
          f"(сдвиг {a.lead * 1000:.0f} мс)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
