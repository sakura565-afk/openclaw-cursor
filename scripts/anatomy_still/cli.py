"""Command-line entry points for masks, validation and the iterative pipeline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .pipeline import AnatomyStillPipeline
from .pose_mask import build_mask_from_image, save_mask
from .recipes import get_recipe


def _rgb(path: str | Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def main() -> int:
    parser = argparse.ArgumentParser(prog="anatomy_still")
    sub = parser.add_subparsers(dest="command", required=True)

    mask_parser = sub.add_parser("mask", help="build a pelvis mask from detected pose")
    mask_parser.add_argument("--image", required=True)
    mask_parser.add_argument("--mask-out", required=True)
    mask_parser.add_argument("--pose-out", default="")
    mask_parser.add_argument("--scene", default="")

    validate_parser = sub.add_parser("validate", help="measure visibility in 0..1")
    validate_parser.add_argument("--image", required=True)
    validate_parser.add_argument("--mask", required=True)

    run_parser = sub.add_parser("run", help="run edit and masked refinements until QC passes")
    run_parser.add_argument("--scene", required=True)
    run_parser.add_argument("--source", required=True)
    run_parser.add_argument("--output-dir", default="anatomy_still_output")
    run_parser.add_argument("--recipes-file", default="")
    run_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if args.command == "mask":
        recipe = get_recipe(args.scene) if args.scene else None
        mask_config = recipe["mask"] if recipe else None
        mask, pose = build_mask_from_image(_rgb(args.image), mask_config)
        save_mask(args.mask_out, mask)
        payload = {
            "image": args.image,
            "mask": args.mask_out,
            "active_pixels": int(np.count_nonzero(mask)),
            "pose": pose.to_dict(),
        }
        if args.pose_out:
            Path(args.pose_out).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "validate":
        from .metrics import validate_anatomy

        result = validate_anatomy(_rgb(args.image), _rgb(args.mask)[:, :, 0])
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.passed else 1
    pipeline = AnatomyStillPipeline(
        args.scene, recipes_path=args.recipes_file or None
    )
    report = pipeline.run(args.source, args.output_dir, dry_run=args.dry_run)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{args.scene}_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
