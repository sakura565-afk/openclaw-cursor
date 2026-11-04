"""Validated access to the scene recipe library."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RECIPES_PATH = Path(__file__).with_name("recipes.json")


def load_recipes(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    source = Path(path) if path else RECIPES_PATH
    with source.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    scenes = payload.get("scenes")
    if not isinstance(scenes, dict) or not scenes:
        raise ValueError("recipes file has no scenes")
    for name, recipe in scenes.items():
        required = {"source", "size", "edit", "mask", "refine", "h3", "status"}
        missing = required - set(recipe)
        if missing:
            raise ValueError(f"recipe {name!r} misses {sorted(missing)}")
        if len(recipe["size"]) != 2 or any(int(v) <= 0 for v in recipe["size"]):
            raise ValueError(f"recipe {name!r} has an invalid size")
        denoise = float(recipe["edit"]["denoise"])
        if not 0.0 < denoise < 1.0:
            raise ValueError(f"recipe {name!r} has invalid edit denoise")
        for lora in recipe["edit"]["loras"]:
            if not lora["name"] or not 0.0 <= float(lora["strength"]) <= 2.0:
                raise ValueError(f"recipe {name!r} has an invalid LoRA")
        if not recipe["refine"]["passes"]:
            raise ValueError(f"recipe {name!r} has no masked-refine passes")
    return scenes


def get_recipe(scene: str, path: str | Path | None = None) -> dict[str, Any]:
    recipes = load_recipes(path)
    try:
        return recipes[scene]
    except KeyError as exc:
        raise ValueError(f"unknown anatomy scene {scene!r}; choose {sorted(recipes)}") from exc
