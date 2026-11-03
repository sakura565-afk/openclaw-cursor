# -*- coding: utf-8 -*-
"""RIFE 24->48 fps on PROD, then mux the audio back from the source clip.

Part of the production chain (FreeVideo/H3 -> SeedVR2 -> RIFE -> mux), so it lives in the
repository rather than only in the scratch directory.


Usage: python rife48.py <src.mp4> <out.mp4>
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from video_pipeline.assembler import interpolate  # noqa: E402

FF = os.environ.get("FFMPEG_BIN", r"C:\Users\user\AppData\Local\hermes\tools\ffmpeg-9.0.1-win32-x64\bin\ffmpeg.exe")
FP = os.environ.get("FFPROBE_BIN", r"C:\Users\user\AppData\Local\hermes\tools\ffmpeg-9.0.1-win32-x64\bin\ffprobe.exe")
PROD = os.environ.get("COMFYUI_PROD_URL", "http://127.0.0.1:8188")


def probe(p):
    import json
    d = json.loads(subprocess.run(
        [FP, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(p)],
        capture_output=True, text=True).stdout)
    v = next(s for s in d["streams"] if s.get("codec_type") == "video")
    a = [s for s in d["streams"] if s.get("codec_type") == "audio"]
    return v, a, d


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    tmp = out.with_name(out.stem + "_rife.mp4")
    v0, _a0, _d0 = probe(src)
    src_fps = float(v0.get("r_frame_rate", "24/1").split("/")[0]) / max(
        1.0, float(v0.get("r_frame_rate", "24/1").split("/")[1]))
    target = 48.0
    # RIFE works in whole multiples: 24->48 is x2, 12->48 is x4.
    multiplier = max(1, int(round(target / max(src_fps, 0.1))))
    print(f"RIFE {src_fps:.3f}->{target:.0f} fps (x{multiplier}): {src.name}", flush=True)
    t0 = time.time()
    interpolate(src, tmp, target, comfyui_url=PROD, multiplier=multiplier)
    print(f"  интерполяция за {time.time()-t0:.0f} с -> {tmp.name}", flush=True)
    v, a, d = probe(tmp)
    dur = float(d["format"]["duration"])
    # An empty or frame-less intermediate makes ffmpeg fail with a confusing
    # "Error parsing options for output file". It happens when the RIFE job
    # could not get the GPU, so check it here and say what actually went wrong.
    if not v.get("nb_frames") or int(v.get("nb_frames")) == 0:
        print(f"ПУСТОЙ ПРОМЕЖУТОЧНЫЙ ФАЙЛ {tmp.name} "
              f"({tmp.stat().st_size if tmp.exists() else 0} байт) - "
              f"RIFE не отработал (занятый GPU?). Исходник не тронут.", flush=True)
        return 1
    r = subprocess.run([FF, "-v", "error", "-y", "-i", str(tmp), "-i", str(src),
                        "-map", "0:v:0", "-map", "1:a:0?",
                        "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("FFMPEG FAIL:", r.stderr[:400])
        return 1
    tmp.unlink(missing_ok=True)
    v, a, d = probe(out)
    print(f"ИТОГ: {v['width']}x{v['height']} {v['nb_frames']} кадров {float(d['format']['duration']):.2f}s "
          f"аудио={len(a)} {out.stat().st_size/2**20:.2f} МБ", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
