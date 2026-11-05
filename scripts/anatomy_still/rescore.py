"""Re-score the anatomy-still runs with the current validator.

Usage: python scripts/anatomy_still/rescore.py <run_dir> [...]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
from PIL import Image

from scripts.anatomy_still.metrics import validate_anatomy


def _rgb(path):
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def rescore(run_dir: Path) -> list[dict]:
    report_path = run_dir / f"{run_dir.name.split('_')[-1]}_report.json"
    if not report_path.exists():
        candidates = list(run_dir.glob("*_report.json"))
        if not candidates:
            return []
        report_path = candidates[0]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = []
    for entry in report.get("passes", []):
        still = entry.get("still")
        mask = entry.get("mask")
        if not still or not mask:
            continue
        if not (os.path.exists(still) and os.path.exists(mask)):
            rows.append({"still": os.path.basename(str(still)), "status": "missing"})
            continue
        rgb = _rgb(still)
        m = _rgb(mask)[:, :, 0]
        if rgb.shape[:2] != m.shape:
            rows.append({"still": os.path.basename(str(still)), "status": "shape_mismatch"})
            continue
        r = validate_anatomy(rgb, m)
        rows.append({
            "still": os.path.basename(str(still)),
            "visibility": r.visibility,
            "cloth_score": r.cloth_score,
            "texture_score": r.texture_score,
            "skin_fraction": r.skin_fraction,
            "passed": r.passed,
        })
    return rows


def main() -> int:
    dirs = [Path(a) for a in sys.argv[1:]]
    for d in dirs:
        print(f"=== {d.name} ===")
        for row in rescore(d):
            if row.get("status"):
                print(f"  {row['still']}: {row['status']}")
            else:
                print(f"  {row['still']:32} visibility={row['visibility']:.3f} "
                      f"cloth={row['cloth_score']:.3f} texture={row['texture_score']:.3f} "
                      f"skin={row['skin_fraction']:.3f} passed={row['passed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
