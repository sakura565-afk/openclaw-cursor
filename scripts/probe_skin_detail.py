# -*- coding: utf-8 -*-
"""Run pixel detail models on real video frames and measure skin microtexture."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

_prod_root = os.environ.get("COMFYUI_PROD_ROOT")
PROD = Path(_prod_root) if _prod_root else None
BASE = "http://127.0.0.1:8188"


def api(path: str, payload: dict | None = None, timeout: int = 120) -> dict:
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def run_model(source: Path, model: str, out: Path) -> Path:
    if PROD is None:
        raise RuntimeError("Set COMFYUI_PROD_ROOT to the ComfyUI directory")
    input_name = f"probe_skin_{int(time.time() * 1000)}_{source.name}"
    shutil.copy2(source, PROD / "input" / input_name)
    with Image.open(source) as image:
        width, height = image.size
    nodes = {
        "1": {"class_type": "LoadImage", "inputs": {"image": input_name}},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
        "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
        "4": {"class_type": "ImageScale", "inputs": {
            "image": ["3", 0], "upscale_method": "lanczos", "width": width, "height": height, "crop": "disabled",
        }},
        "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": f"probe_skin_{out.stem}"}},
    }
    response = api("/prompt", {"prompt": nodes, "client_id": "probe-skin-detail"})
    prompt_id = response["prompt_id"]
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        history = api(f"/history/{prompt_id}")
        entry = history.get(prompt_id)
        if entry:
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(json.dumps(status.get("messages", []), ensure_ascii=False))
            for output in entry.get("outputs", {}).values():
                for item in output.get("images", []):
                    result = PROD / "output" / item.get("subfolder", "") / item["filename"]
                    if result.exists():
                        out.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(result, out)
                        return out
        time.sleep(1)
    raise TimeoutError(f"detail model {model} timed out")


def laplacian(gray: np.ndarray) -> np.ndarray:
    return (
        -4 * gray[1:-1, 1:-1]
        + gray[:-2, 1:-1] + gray[2:, 1:-1]
        + gray[1:-1, :-2] + gray[1:-1, 2:]
    )


def crop_metric(path: Path, box: tuple[int, int, int, int]) -> dict[str, float]:
    rgb = np.asarray(Image.open(path).convert("RGB").crop(box), dtype=np.float32)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    mask = ((cb > 77) & (cb < 127) & (cr > 133) & (cr < 173) & (r > g * 1.02))
    values = np.abs(laplacian(y))
    selected = values[mask[1:-1, 1:-1] & mask[:-2, 1:-1] & mask[2:, 1:-1] & mask[1:-1, :-2] & mask[1:-1, 2:]]
    if selected.size < 100:
        selected = values.ravel()
    return {"microtexture": round(float(selected.mean()), 4), "selected_pixels": int(selected.size)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--models", nargs="+", default=[
        "1x-ITF-SkinDiffDetail-Lite-v1.pth",
        "1xSkinContrast-High-SuperUltraCompact.pth",
        "4x-ClearRealityV1.safetensors",
        "4x-UltraSharp.pth",
    ])
    ap.add_argument("--box", type=int, nargs=4, default=[350, 783, 600, 912])
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    box = tuple(args.box)
    rows = [{"model": "source", "file": str(args.source), **crop_metric(args.source, box)}]
    for model in args.models:
        stem = Path(model).stem.replace("-", "_")
        out = args.out_dir / f"{args.source.stem}_{stem}.png"
        started = time.monotonic()
        run_model(args.source, model, out)
        row = {"model": model, "file": str(out), "seconds": round(time.monotonic() - started, 2), **crop_metric(out, box)}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    report = args.out_dir / f"{args.source.stem}_metrics.json"
    report.write_text(json.dumps({"source": str(args.source), "box": box, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
