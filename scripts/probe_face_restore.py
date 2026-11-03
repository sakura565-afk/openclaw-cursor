# -*- coding: utf-8 -*-
"""One-frame CodeFormer/GFPGAN face-restoration probe through ReActor."""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from probe_skin_detail import PROD, api, crop_metric


def run_restore(source: Path, model: str, out: Path) -> None:
    if PROD is None:
        raise RuntimeError("Set COMFYUI_PROD_ROOT to the ComfyUI directory")
    input_name = f"probe_face_{int(time.time() * 1000)}_{source.name}"
    shutil.copy2(source, PROD / "input" / input_name)
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": input_name}},
        "2": {"class_type": "ReActorRestoreFace", "inputs": {
            "image": ["1", 0], "facedetection": "retinaface_resnet50", "model": model,
            "visibility": 1.0, "codeformer_weight": 0.7,
        }},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0], "filename_prefix": "probe_face_restore"}},
    }
    prompt_id = api("/prompt", {"prompt": graph, "client_id": "probe-face-restore"})["prompt_id"]
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        entry = api(f"/history/{prompt_id}").get(prompt_id)
        if entry:
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(json.dumps(status.get("messages", []), ensure_ascii=False))
            for output in entry.get("outputs", {}).values():
                for item in output.get("images", []):
                    result = PROD / "output" / item.get("subfolder", "") / item["filename"]
                    if result.exists():
                        shutil.copy2(result, out)
                        return
        time.sleep(1)
    raise TimeoutError(f"face restoration {model} timed out")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--crop", default="235,160,466,400")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    box = tuple(map(int, args.crop.split(",")))
    print("source", crop_metric(args.source, box), flush=True)
    for model in ["codeformer-v0.1.0.pth", "GFPGANv1.4.pth"]:
        out = args.out_dir / f"{Path(model).stem}.png"
        run_restore(args.source, model, out)
        print(model, crop_metric(out, box), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
