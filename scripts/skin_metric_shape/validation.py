"""Repeat the frozen classifier on independently seeded constructive plates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from scripts.skin_metric_groundtruth.synthetic import build_cases
from scripts.skin_texture_metrics import measure_image

DEFAULT_VALIDATION_SEEDS = (4103, 5102, 6102, 7102, 8102, 9102, 10102, 11102)


def validate_synthetic_seeds(seeds: Sequence[int] = DEFAULT_VALIDATION_SEEDS) -> dict:
    per_seed = []
    for seed in seeds:
        rows = []
        for case in build_cases(seed=int(seed)):
            measured = measure_image(case.image)
            rows.append({
                "case_id": case.case_id,
                "truth": case.expected_class,
                "metric": measured["classification"],
                "match": measured["classification"] == case.expected_class,
                "component_density_per_10k": measured["shape_component_density_per_10k"],
            })
        pores = [row["component_density_per_10k"] for row in rows if row["truth"] == "pores"]
        sand = [row["component_density_per_10k"] for row in rows if row["truth"] == "digital_sand"]
        per_seed.append({
            "seed": int(seed),
            "total": len(rows),
            "matches": sum(row["match"] for row in rows),
            "pore_density_max": max(pores),
            "sand_density_min": min(sand),
            "mismatches": [row["case_id"] for row in rows if not row["match"]],
        })
    return {
        "tool": "scripts.skin_metric_shape.validation",
        "independent_of_threshold_core_seed": True,
        "summary": {
            "seeds": len(per_seed),
            "total": sum(row["total"] for row in per_seed),
            "matches": sum(row["matches"] for row in per_seed),
            "all_pore_density_max": max(row["pore_density_max"] for row in per_seed),
            "all_sand_density_min": min(row["sand_density_min"] for row in per_seed),
        },
        "per_seed": per_seed,
    }


def write_validation_report(out_dir: str | Path) -> tuple[Path, Path]:
    report = validate_synthetic_seeds()
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "synthetic_validation.json"
    markdown_path = output / "synthetic_validation.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = report["summary"]
    lines = [
        "# Independent synthetic-seed validation",
        "",
        f"Frozen thresholds: **{summary['matches']}/{summary['total']}** across "
        f"{summary['seeds']} non-calibration seeds.",
        "",
        "| seed | matches | pore density max | sand density min | mismatches |",
        "|---:|---:|---:|---:|---|",
    ]
    for row in report["per_seed"]:
        lines.append(
            f"| {row['seed']} | {row['matches']}/{row['total']} | "
            f"{row['pore_density_max']:.3f} | {row['sand_density_min']:.3f} | "
            f"{', '.join(row['mismatches']) or 'none'} |"
        )
    lines.append("")
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, markdown_path
