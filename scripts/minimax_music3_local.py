"""Run MiniMax Music 3 locally on a 16 GB GPU.

The weights live in a local snapshot (see MODEL_DIR). Diffusers needs the
PR commit that carries the modular Music3 pipeline; the released version does
not have it yet.

Low VRAM path: auto CPU offload (~22 GB) plus leaf-level group offloading of
the language model, which streams it layer by layer and keeps VRAM near 8 GB.
Generation is then slow but fits.

Usage:
  python scripts/minimax_music3_local.py --out song.wav --seconds 20 \
      --prompt "Genre: acoustic pop..." --lyrics "[verse]..."
"""
from __future__ import annotations

import argparse
import os
import sys
import time

DEFAULT_MODEL_DIR = os.environ.get("MINIMAX_MUSIC3_DIR", "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prompt", default="")
    ap.add_argument("--prompt-file", default="")
    ap.add_argument("--lyrics", default="")
    ap.add_argument("--lyrics-file", default="")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--steps", type=int, default=0,
                    help="0 = pipeline default")
    ap.add_argument("--no-offload", action="store_true",
                    help="load everything onto the GPU (needs 24 GB+)")
    a = ap.parse_args()

    if not a.model_dir:
        print("Set MINIMAX_MUSIC3_DIR to the local Music3 snapshot directory", file=sys.stderr)
        return 2
    if not os.path.isdir(a.model_dir):
        print(f"нет каталога модели: {a.model_dir}", file=sys.stderr)
        return 2

    def _read(path: str, direct: str) -> str:
        if direct:
            return direct
        if path and os.path.exists(path):
            return open(path, encoding="utf-8").read().strip()
        return ""

    prompt = _read(a.prompt_file, a.prompt)
    lyrics = _read(a.lyrics_file, a.lyrics)
    if not prompt:
        print("нужен --prompt или --prompt-file", file=sys.stderr)
        return 2

    try:
        import torch
    except ImportError:
        print("нужен torch", file=sys.stderr)
        return 2
    try:
        from diffusers import ComponentsManager, ModularPipeline
    except ImportError as e:
        print("нужен diffusers с Music3 (PR-коммит):", e, file=sys.stderr)
        return 2
    try:
        import soundfile as sf
    except ImportError:
        print("нужен soundfile", file=sys.stderr)
        return 2

    if not torch.cuda.is_available():
        print("CUDA недоступна", file=sys.stderr)
        return 2

    t0 = time.time()
    if a.no_offload:
        pipe = ModularPipeline.from_pretrained(a.model_dir)
        pipe.load_components(dtype=torch.bfloat16)
        pipe.to("cuda")
    else:
        manager = ComponentsManager()
        manager.enable_auto_cpu_offload(device="cuda")
        pipe = ModularPipeline.from_pretrained(a.model_dir, components_manager=manager)
        pipe.load_components(dtype=torch.bfloat16)
        # Only needed below ~22 GB of VRAM - slower, but fits in 8 GB.
        try:
            from diffusers.hooks import apply_group_offloading
            apply_group_offloading(
                pipe.language_model,
                onload_device=torch.device("cuda"),
                offload_type="leaf_level",
                use_stream=True,
            )
            print("group offload: leaf_level + stream", flush=True)
        except Exception as e:
            print("group offload не применился:", e, flush=True)
    print(f"модель загружена за {time.time() - t0:.0f} с", flush=True)

    kwargs = dict(
        prompt=prompt,
        audio_duration=a.seconds,
        generator=torch.Generator("cuda").manual_seed(a.seed),
        output="audios",
    )
    if lyrics:
        kwargs["lyrics"] = lyrics
    if a.steps:
        kwargs["num_inference_steps"] = a.steps

    t1 = time.time()
    audio = pipe(**kwargs)[0]
    dt = time.time() - t1
    data = audio.T.float().cpu().numpy()
    sf.write(a.out, data, pipe.sampling_rate)
    print(f"готово: {a.out} ({data.shape[-1] / pipe.sampling_rate:.1f} с аудио, "
          f"генерация {dt:.0f} с)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
