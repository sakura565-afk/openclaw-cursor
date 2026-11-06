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
# H3 renders at 24 fps; the clip length in frames follows the audio length.
FPS = 24
sys.path.insert(0, str(REPO))


def _silent_audio(path: Path, seconds: float = 1.0, rate: int = 44100) -> Path:
    """A silent wav. H3 generates its own sound; this only satisfies the mux.

    The length of this file decides the length of the render: H3 follows the
    audio, so a one-second placeholder produced a one-second clip (21 frames)
    no matter what length was requested. The caller therefore passes the
    duration the clip should have.
    """
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


def _pick_best_frame(video: Path, out_png: Path, mask_png: Path | None,
                     step: int = 4) -> dict:
    """Choose the frame where the anatomy is exposed.

    The validator needs the pose mask, which needs mediapipe and therefore a
    CPU environment. When it is not available the first frame is used and the
    limitation is reported, rather than pretending the pick was measured.
    """
    if mask_png is None or not Path(mask_png).exists():
        print("нет маски - беру первый кадр (выбор кадра не измерен)",
              file=sys.stderr)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video),
                        "-frames:v", "1", str(out_png)], capture_output=True, timeout=300)
        return {"frame": 0, "visibility": float("nan")}

    from scripts.anatomy_still.pick_still import extract, load_mask, load_rgb
    from scripts.anatomy_still.metrics import validate_anatomy

    mask = load_mask(Path(mask_png))
    work = out_png.parent / "_pick"
    work.mkdir(parents=True, exist_ok=True)
    total = int(subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, timeout=900).stdout.strip() or 0)
    best = None
    for idx in range(0, total, step):
        frame = extract(str(video), idx, work / f"f{idx:05d}.png")
        image = load_rgb(frame)
        # The mask was built from the source photo, which has a different size
        # than the render. Scale it instead of rebuilding it: the pose is the
        # same, only the resolution differs.
        if mask.shape[:2] != image.shape[:2]:
            import cv2
            scaled = cv2.resize(mask, (image.shape[1], image.shape[0]),
                                interpolation=cv2.INTER_NEAREST)
        else:
            scaled = mask
        m = validate_anatomy(image, scaled)
        if best is None or m.visibility > best["visibility"]:
            best = {"frame": idx, "visibility": m.visibility}
    if best is None:
        best = {"frame": 0, "visibility": float("nan")}
    src = work / f"f{best['frame']:05d}.png"
    shutil.copy2(src, out_png)
    return best


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
    pick_best_frame: bool = False,
    mask_png: Path | None = None,
) -> Path:
    """Render with H3 fl2va and write frame 0 as a still."""
    source = Path(source)
    out_png = Path(out_png)
    work = out_png.parent / "_h3_work"
    work.mkdir(parents=True, exist_ok=True)

    prompt_file = work / "prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    # H3 takes the clip length from the audio, so the placeholder has to be as
    # long as the requested clip - a one-second placeholder gave a 21-frame
    # render regardless of the length that was asked for.
    audio = _silent_audio(work / "silent.wav", seconds=length / float(FPS))
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
        if not tail and log is not None:
            # The runner wrote everything to its log file, so stderr is
            # empty and the failure came out as "h3_i2v_run failed (1): "
            # with nothing behind it.
            try:
                tail = Path(log).read_text(encoding="utf-8", errors="replace")[-1500:]
            except OSError:
                tail = ""
        raise RuntimeError(f"h3_i2v_run failed ({r.returncode}): {tail}")
    if not video.exists():
        raise RuntimeError("h3 render produced no video")

    out_png.parent.mkdir(parents=True, exist_ok=True)

    if pick_best_frame:
        # The action takes time: in the approved kitchen clip frame 0 still
        # shows the apron down and the anatomy only appears around frame 108.
        # A still therefore has to be chosen from the clip.
        best = _pick_best_frame(video, out_png, mask_png)
        print(f"still: {best['frame']} visibility={best['visibility']:.3f}")
        return out_png

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
    ap.add_argument("--pick-best-frame", action="store_true",
                    help="choose the frame where the anatomy is exposed instead "
                         "of taking the first one")
    ap.add_argument("--mask", default="", help="pose mask used by the frame picker")
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
        pick_best_frame=a.pick_best_frame,
        mask_png=Path(a.mask) if a.mask else None,
    )
    if a.pick_best_frame:
        print(f"still: {out} ({out.stat().st_size} байт) - лучший кадр по маске "
              f"(взгляни сам: anatomy_still/pipeline.py делает это по умолчанию)")
    else:
        print(f"still: {out} ({out.stat().st_size} байт) - это КАДР 0, а не "
              f"лучший: анатомия в первом кадре ещё не раскрылась. "
              f"Для кадра с анатомией используй pipeline.py или "
              f"--pick-best-frame --mask <маска>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
