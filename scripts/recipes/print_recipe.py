"""Render-command printer for the approved H3 scene registry.

This module never submits a render.  ``--check-loras`` performs only a read-only
/object_info request; an unreachable server is reported as unverified, never as
an empty or successful check.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence

RECIPE_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = RECIPE_DIR / "registry.json"
DEFAULT_COMFYUI_URL = "http://127.0.0.1:8188"


def load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def load_recipe(scene_id: str) -> dict:
    for scene in load_registry()["scenes"]:
        if scene["id"] == scene_id:
            return scene
    raise ValueError(f"unknown scene: {scene_id}")


def _lora_argument(scene: dict) -> str:
    return json.dumps(
        [
            {"n": lora["name"], "sm": lora["strength_model"], "sc": lora["strength_clip"]}
            for lora in scene["loras"]
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def render_command(scene: dict) -> str:
    """Return a git-bash command, with environment variables left expandable."""

    image_token = f"RECIPE_IMAGE_{scene['id']}"
    output_token = f"RECIPE_OUTPUT_{scene['id']}"
    tokens = [
        "python",
        "scripts/h3_i2v_run.py",
        "--image",
        image_token,
        "--prompt-file",
        f"scripts/recipes/prompts/{scene['id']}.txt",
        "--clip-name",
        scene["text_encoder"],
        "--loras-json",
        _lora_argument(scene),
        "--negative",
        scene["negative"],
    ]
    for key, flag in (
        ("width", "--width"),
        ("height", "--height"),
        ("length", "--length"),
        ("seed", "--seed"),
        ("steps", "--steps"),
    ):
        if scene[key] is not None:
            tokens.extend((flag, str(scene[key])))
    tokens.extend((
        "--sampler", scene["sampler"],
        "--scheduler", scene["scheduler"],
        "--prefix", scene["id"].lower(),
        "--audio", "scripts/recipes/silence_7_30.wav",
        "--output", output_token,
    ))
    command = shlex.join(tokens)
    command = command.replace(image_token, f'"${scene["image_env"]}"')
    command = command.replace(
        output_token, f'"${{HERMES_SCRATCH_DIR:-.}}/{scene["id"]}.mp4"'
    )
    return command


def _available_loras(base_url: str, timeout: float) -> list[str]:
    url = base_url.rstrip("/") + "/object_info"
    raw = urllib.request.urlopen(url, timeout=timeout).read().decode("utf-8", "replace")
    info = json.loads(raw)
    combo = info["LoraLoader"]["input"]["required"]["lora_name"][0]
    if not isinstance(combo, list):
        raise ValueError("LoraLoader.lora_name is not a COMBO list")
    return [str(name) for name in combo]


def check_loras(scenes: Sequence[dict], base_url: str, timeout: float) -> tuple[list[str], list[str]]:
    """Return (checked, missing), raising on an unavailable or invalid server."""

    expected = sorted({lora["name"] for scene in scenes for lora in scene["loras"]})
    available = set(_available_loras(base_url, timeout))
    return expected, [name for name in expected if name not in available]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", help="scene id; omit with --check-loras to check all")
    parser.add_argument("--list", action="store_true", help="list approved scene ids")
    parser.add_argument(
        "--check-loras", action="store_true",
        help="compare exact registry names with the live ComfyUI LoraLoader COMBO",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("COMFYUI_PROD_URL", DEFAULT_COMFYUI_URL),
        help="ComfyUI origin (or COMFYUI_PROD_URL)",
    )
    parser.add_argument("--timeout", type=float, default=3.0, help="object_info timeout in seconds")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.list:
            for scene in load_registry()["scenes"]:
                print(scene["id"])
            return 0
        if args.scene:
            scenes = [load_recipe(args.scene)]
        elif args.check_loras:
            scenes = load_registry()["scenes"]
        else:
            raise ValueError("provide --scene <id>, --list, or --check-loras")

        if args.check_loras:
            try:
                checked, missing = check_loras(scenes, args.base_url, args.timeout)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, urllib.error.URLError) as exc:
                print(f"ComfyUI недоступен или ответ не распознан: {exc}")
                print("Сверка имён LoRA не выполнена; это НЕ проверка на отсутствие файлов.")
                return 2
            for name in checked:
                print(f"{'MISSING' if name in missing else 'OK'} {name}")
            print(f"{len(checked)} LoRA names checked against {args.base_url}")
            return 1 if missing else 0

        scene = scenes[0]
        if scene["unrecorded"]:
            print(
                f"{scene['id']}: unrecorded in source: {', '.join(scene['unrecorded'])}; "
                "null render flags are omitted and the runner defaults will apply.",
                file=sys.stderr,
            )
        print(render_command(scene))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
