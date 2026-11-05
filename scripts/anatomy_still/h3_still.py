"""Render an anatomy still with H3 instead of an image editor.

Why this exists: Qwen-Image-Edit censors the region. Measured on the chef
scene, inside the pose mask the source kept a texture contrast of 5.23 and the
Qwen edit left 1.15 - a flat grey fill at the same brightness. That is the
model's own filter, not a bug in the pipeline, so no amount of masked
refinement gets around it.

H3 with a minimal LoRA stack is the path that does work for this scene: one
action LoRA plus one anatomy LoRA, and the anatomy written as a single
continuous cleft instead of a list of openings.

This module runs the fl2va render and takes its first frame as the still.

Usage:
  python scripts/anatomy_still/h3_still.py --source chef_src.jpg \
      --prompt-file chef.txt --loras-json '[...]' --out still.png
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def _silent_audio(path: Path, seconds: float = 1.0, rate: int = 44100) -> Path:
    """A silent wav. H3 generates its own sound; this only satisfies the mux."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


def _normalise_loras(loras: list[dict]) -> list[dict]:
    """Accept the readable form and emit what the H3 runner expects.

    h3_i2v_run.py wants {"n": name, "sm": strength_model, "sc": strength_clip};
    the recipe files use {"name": ..., "strength": ...}. Convert here so the
    caller does not have to know the runner's internal spelling.
    """
    out = []
    for lo in loras:
        if "n" in lo:
            out.append(lo)
            continue
        name = lo.get("name")
        if not name:
            raise ValueError(f"LoRA without a name: {lo}")
        sm = float(lo.get("strength", lo.get("sm", 1.0)))
        sc = float(lo.get("strength_clip", lo.get("sc", sm)))
        out.append({"n": name, "sm": sm, "sc": sc})
    return out


def render_still(
    *,
    source: Path,
    prompt: str,
    loras: list[dict],
    out_png: Path,
    width: int = 736,
    height: int = 1120,
    length: int = 33,
    steps: int = 20,
    seed: int = 505050,
    sampler: str = "euler",
    scheduler: str = "simple",
    negative: str = "",
    extra_args: list[str] | None = None,
    python: str | None = None,
    log: Path | None = None,
) -> Path:
    """Render with H3 fl2va and write frame 0 as a still."""
    source = Path(source)
    out_png = Path(out_png)
    work = out_png.parent / "_h3_work"
    work.mkdir(parents=True, exist_ok=True)

    prompt_file = work / "prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    audio = _silent_audio(work / "silent.wav")
    video = work / "render.mp4"

    runner = REPO / "scripts" / "h3_i2v_run.py"
    cmd = [
        python or sys.executable, str(runner),
        "--image", str(source),
        "--prompt-file", str(prompt_file),
        "--loras-json", json.dumps(_normalise_loras(loras)),
        "--audio", str(audio),
        "--output", str(video),
        "--width", str(width),
        "--height", str(height),
        "--length", str(length),
        "--steps", str(steps),
        "--seed", str(seed),
        "--sampler", sampler,
        "--scheduler", scheduler,
        "--prefix", "anatomy_h3",
    ]
    if negative:
        cmd += ["--negative", negative]
    if extra_args:
        cmd += list(extra_args)

    stream = open(log, "w", encoding="utf-8") if log else None
    try:
        r = subprocess.run(
            cmd,
            stdout=stream if stream is not None else subprocess.PIPE,
            stderr=subprocess.STDOUT if stream is not None else subprocess.PIPE,
            text=True,
            timeout=7200,
            cwd=str(REPO),
        )
    finally:
        if stream:
            stream.close()
    if r is not None and r.returncode != 0:
        tail = (r.stderr or r.stdout or "")[-1500:]
        raise RuntimeError(f"h3_i2v_run failed ({r.returncode}): {tail}")
    if not video.exists():
        raise RuntimeError("h3 render produced no video")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video),
                    "-frames:v", "1", str(out_png)], capture_output=True, timeout=300)
    if not out_png.exists():
        raise RuntimeError("could not extract the first frame")
    return out_png


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--prompt", default="")
    ap.add_argument("--prompt-file", default="")
    ap.add_argument("--loras-json", default="[]")
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=736)
    ap.add_argument("--height", type=int, default=1120)
    ap.add_argument("--length", type=int, default=33)
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--seed", type=int, default=505050)
    ap.add_argument("--sampler", default="euler")
    ap.add_argument("--scheduler", default="simple")
    ap.add_argument("--negative", default="")
    ap.add_argument("--log", default="")
    a = ap.parse_args()

    prompt = a.prompt
    if a.prompt_file:
        prompt = Path(a.prompt_file).read_text(encoding="utf-8")
    out = render_still(
        source=Path(a.source),
        prompt=prompt,
        loras=json.loads(a.loras_json),
        out_png=Path(a.out),
        width=a.width, height=a.height, length=a.length, steps=a.steps,
        seed=a.seed, sampler=a.sampler, scheduler=a.scheduler,
        negative=a.negative,
        log=Path(a.log) if a.log else None,
    )
    print(f"still: {out} ({out.stat().st_size} байт)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
