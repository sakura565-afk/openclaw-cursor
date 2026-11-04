"""CLI for generating and calibrating the independent skin-metric set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .blind import DEFAULT_BLIND_SEED, build_blind_set
from .calibration import build_calibration_report, write_calibration_report
from .synthetic import DEFAULT_SEED, DEFAULT_SIZE, generate_dataset


def default_out_dir() -> Path:
    return Path(__file__).resolve().parent / "artifacts"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build independent synthetic skin-metric truth and blind sheets (CPU only)."
    )
    parser.add_argument("--out", default=str(default_out_dir()), help="artefact directory")
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--blind-seed", type=int, default=DEFAULT_BLIND_SEED)
    parser.add_argument(
        "--visual-labels",
        default="",
        help="optional blind judge JSON; labels are compared without changing thresholds",
    )
    args = parser.parse_args(argv)
    out = Path(args.out)
    entries = generate_dataset(out, size=args.size, seed=args.seed)
    blind_dir = out / "blind"
    build_blind_set(out / "synthetic_manifest.json", blind_dir, seed=args.blind_seed)
    report = build_calibration_report(
        out / "synthetic_manifest.json",
        blind_key_path=blind_dir / "blind_key.json",
        visual_labels_path=Path(args.visual_labels) if args.visual_labels else None,
    )
    json_path, _ = write_calibration_report(report, out)
    if args.visual_labels:
        blind_report_path = blind_dir / "blind_report.json"
        blind_report = json.loads(blind_report_path.read_text(encoding="utf-8"))
        blind_report.update(
            {
                "visual_status": "labels_loaded",
                "visual_labels": Path(args.visual_labels).name,
                "visual_judge": report["visual_assessment"]["judge"],
                "visual_is_human": report["visual_assessment"]["is_human"],
            }
        )
        blind_report_path.write_text(
            json.dumps(blind_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    summary = report["summary"]
    print(f"cases: {len(entries)} ({summary['core_truth_matches']}/{summary['core_total']} core matches)")
    print(
        f"constructive matches: {summary['truth_matches']}/{summary['total']}; "
        f"mismatches: {', '.join(summary['truth_mismatches']) or 'none'}"
    )
    print(
        f"visual labels: {summary['visual_total']}; "
        f"metric/visual mismatches: {len(summary['visual_metric_mismatches'])}"
    )
    print(f"blind sheet: {blind_dir / 'blind_sheet.png'}")
    print(f"calibration: {json_path}")
    return 0
