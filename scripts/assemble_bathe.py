# -*- coding: utf-8 -*-
"""Assemble the bath action clip from three segments.

A = bathe_v2_raw.mp4  0..2.95s  - legs spread (the open pose at 2.95s is B's first frame)
B = bathe_hand_raw.mp4 full     - hand working at the lower body, legs open
C = bathe_climax_raw.mp4 full   - climax and relaxation (first frame = B's last frame)

Each segment is 736x1120, 24 fps, with audio. Re-encoded on join for clean timestamps.
"""
import os
import subprocess
import sys

S = os.environ.get(
    "HERMES_SCRATCH_DIR",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "cache", "scratch"),
)
SEGMENTS = [
    (os.path.join(S, "bathe_v2_raw.mp4"), 2.95),
    (os.path.join(S, "bathe_hand_raw.mp4"), None),
    (os.path.join(S, "bathe_climax_raw.mp4"), None),
]
OUT = os.path.join(S, "bathe_full_raw.mp4")


def probe(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
                        "-show_entries", "format=duration", "-of", "default=nw=1", p],
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip().replace("\n", " ")


def main():
    for p, cut in SEGMENTS:
        if not os.path.exists(p):
            print("нет сегмента:", p, flush=True)
            return 1
        print(f"  {os.path.basename(p)} cut={cut}: {probe(p)}", flush=True)

    inputs = []
    for p, _ in SEGMENTS:
        inputs += ["-i", p]
    vparts, aparts, concat_v, concat_a = [], [], [], []
    for i, (_, cut) in enumerate(SEGMENTS):
        trim = f"trim=0:{cut}," if cut else ""
        atrim = f"atrim=0:{cut}," if cut else ""
        vparts.append(f"[{i}:v]{trim}setpts=PTS-STARTPTS,fps=24,format=yuv420p[v{i}]")
        aparts.append(f"[{i}:a]{atrim}asetpts=PTS-STARTPTS[a{i}]")
        concat_v.append(f"[v{i}]")
        concat_a.append(f"[a{i}]")
    n = len(SEGMENTS)
    fc = ";".join(vparts + aparts) + ";" + "".join(concat_v) + f"concat=n={n}:v=1:a=0[v];" \
         + "".join(concat_a) + f"concat=n={n}:v=0:a=1[aud]"

    cmd = ["ffmpeg", "-y", "-v", "error", *inputs,
           "-filter_complex", fc, "-map", "[v]", "-map", "[aud]",
           "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", OUT]
    subprocess.run(cmd, check=True, timeout=1200)
    print("сшито:", OUT, os.path.getsize(OUT) // 1024, "КБ", flush=True)
    print(probe(OUT), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
