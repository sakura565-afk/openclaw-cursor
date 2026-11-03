#!/usr/bin/env python
"""Generate a long clip as chained H3 chunks instead of one long shot.

One H3 block covers roughly 10-15 s; past that the face drifts, because the
model has no way to re-anchor on the frame it produced earlier. The fix is
to let the model do what it was trained to do: every chunk starts on a
supplied frame, and the frame it ends on becomes the first frame of the next
chunk. fl2va is the checkpoint that actually LANDS on a supplied frame
(ref2va only nudges toward it, about 10 dB weaker, measured by the model
author).

Per the measured recipe:
  - chunk <= ~30-40 s, otherwise the texture ratchet at the joins becomes
    visible;
  - a per-chunk seed, not one seed for the whole clip;
  - the character description repeated verbatim in every chunk;
  - the duplicated boundary frame trimmed;
  - the audio welded with a 40 ms equal-power crossfade.

Usage:
    python scripts/h3_chain_video.py --image still.png --prompt-file p.txt \
        --audio track.m4a --output out.mp4 --chunks 3
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_paths  # type: ignore  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PY = REPO / ".venv" / "Scripts" / "python.exe"
RUNNER = REPO / "scripts" / "h3_i2v_run.py"
# H3 accepts clip lengths of 17k+5 frames; 56 is a safe short block.
VALID_LENGTH = 56


def run(cmd: list[str], timeout: int = 5400) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=str(REPO), env={**os.environ, "PYTHONPATH": str(REPO)})
    tail = (proc.stdout or "")[-1500:] + (proc.stderr or "")[-800:]
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}):\n{tail}")
    print(tail.strip()[-500:], flush=True)


def first_frame(video: Path, out_png: Path) -> Path:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-frames:v", "1",
                    str(out_png)], check=True, timeout=600)
    return out_png


def build_audio_weld_filters(boundaries: list[int]) -> list[str]:
    """Filter graph that welds an audio track at the chunk boundaries.

    The track is cut at each boundary and the pieces are joined with a 40 ms
    equal-power crossfade, so there is neither a click nor a gap.

    Two ffmpeg facts this encodes, both learned the hard way:
    - the equal-power curve is `qsin`; `c1=equal` is rejected outright
      (`tri` and `exp` are the other valid curve names);
    - filter labels already carry their brackets, so the graph reads
      `[s0][s1]acrossfade=...`. Stripping one bracket yields
      "No such filter".
    """
    edges = [0] + list(boundaries) + [None]
    filters: list[str] = []
    labels: list[str] = []
    for i in range(len(edges) - 1):
        seg = f"[0:a]atrim=start={edges[i]}"
        if edges[i + 1] is not None:
            seg += f":end={edges[i + 1]}"
        filters.append(seg + f",asetpts=PTS-STARTPTS[s{i}]")
        labels.append(f"[s{i}]")
    cur = labels[0]
    for i in range(1, len(labels)):
        out_lab = f"[x{i}]"
        filters.append(
            f"{cur}{labels[i]}"
            f"acrossfade=d=0.04:c1=qsin:c2=qsin{out_lab}")
        cur = out_lab
    filters.append(f"{cur}apad=pad_dur=0.5[aout]")
    return filters


def weld_audio(audio: Path, boundaries: list[int], out: Path) -> Path:
    """Write the welded audio track to `out`."""
    if not boundaries:
        shutil.copy2(audio, out)
        return out
    filters = build_audio_weld_filters(boundaries)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(audio), "-filter_complex", ";".join(filters),
         "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", str(out)],
        check=True, timeout=1800)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="first still (already in ComfyUI/input or a path)")
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--chunks", type=int, default=3)
    ap.add_argument("--length", type=int, default=VALID_LENGTH, help="frames per chunk")
    ap.add_argument("--width", type=int, default=736)
    ap.add_argument("--height", type=int, default=1120)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--work", default="")
    ap.add_argument("--server", default="")
    args = ap.parse_args()

    work = Path(args.work) if args.work else local_paths.SCRATCH / "chain_run"
    work.mkdir(parents=True, exist_ok=True)

    still = Path(args.image)
    if not still.exists():
        # Assume it already lives in ComfyUI's input dir.
        still = local_paths.PROD_IN / Path(args.image).name
        if not still.exists():
            print("still not found:", args.image, file=sys.stderr)
            return 2

    chunk_paths: list[Path] = []
    for i in range(args.chunks):
        out = work / f"chunk_{i:02d}.mp4"
        cmd = [str(PY), "-u", str(RUNNER),
               "--image", str(still),
               "--width", str(args.width), "--height", str(args.height),
               "--length", str(args.length),
               "--seed", str(args.seed + i),          # per-chunk seed
               "--steps", str(args.steps),
               "--prompt-file", args.prompt_file,
               "--prefix", f"chain{i:02d}",
               "--audio", args.audio,
               "--output", str(out),
               "--wait", "5400"]
        if args.server:
            cmd += ["--server", args.server]
        print(f"=== chunk {i + 1}/{args.chunks} (seed {args.seed + i}) ===", flush=True)
        run(cmd)
        if not out.exists():
            raise RuntimeError(f"chunk {i} produced no file")
        chunk_paths.append(out)
        # The frame this chunk ended on becomes the frame the next one starts
        # on. The last chunk of the previous shot is already the boundary, so
        # the next chunk's first frame is a duplicate and gets trimmed on
        # concat.
        still = first_frame(out, work / f"still_{i:02d}.png") if i + 1 < args.chunks else still

    # Concat without re-encoding the video where possible, trimming the
    # duplicated boundary frame of every chunk after the first.
    list_file = work / "concat.txt"
    with open(list_file, "w", encoding="utf-8") as fh:
        for i, p in enumerate(chunk_paths):
            if i == 0:
                fh.write(f"file '{p.as_posix()}'\n")
            else:
                # drop the first frame of the chunk: it duplicates the last
                # frame of the previous one
                trimmed = work / f"trim_{i:02d}.mp4"
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-i", str(p), "-vf", "select=gte(n\\,1)",
                     "-an", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(trimmed)],
                    check=True, timeout=1800)
                fh.write(f"file '{trimmed.as_posix()}'\n")
    video_only = work / "video.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(list_file), "-c", "copy", str(video_only)],
                   check=True, timeout=1800)

    # Audio weld: the source track is cut at the chunk boundaries and joined
    # with a 40 ms equal-power crossfade so there is no click and no gap.
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    boundaries = [args.length * (i + 1) for i in range(args.chunks - 1)]
    weld_audio(Path(args.audio), boundaries, work / "audio.m4a")

    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(video_only), "-i", str(work / "audio.m4a"),
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest",
         str(out)], check=True, timeout=1800)
    print("saved:", out, out.stat().st_size, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
