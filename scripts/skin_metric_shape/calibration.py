"""Human-labelled blind calibration and candidate comparison reports."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from scripts.skin_texture_metrics import measure_image
from scripts.skin_texture_metrics.metrics import legacy_classify

from .shape import (
    SHAPE_MAX_COMPONENT_DENSITY,
    SHAPE_MIN_COMPONENT_DENSITY,
    SHAPE_MIN_PORE_RMS,
    measure_shape,
)

CORE_CASE_IDS = {"plastic_core", "pores_core", "noise_core", "mixed_core"}
CANDIDATE_DIRECTIONS = {
    "peak_area_fraction": "lower_is_pore_like",
    "component_density_per_10k": "lower_is_pore_like",
    "median_component_width_px": "higher_is_pore_like",
    "pit_noise_area_ratio": "higher_is_pore_like",
    "laplacian_peak_correlation": "lower_is_pore_like",
}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _range(values: list[float]) -> list[float]:
    return [min(values), max(values)]


def _candidate_comparison(rows: list[dict]) -> dict:
    compared = []
    for name, direction in CANDIDATE_DIRECTIONS.items():
        pores = [row["shape"][name] for row in rows if row["truth"] == "pores"]
        sand = [row["shape"][name] for row in rows if row["truth"] == "digital_sand"]
        if direction == "lower_is_pore_like":
            separation = min(sand) / max(max(pores), 1e-9)
        else:
            separation = min(pores) / max(max(sand), 1e-9)
        compared.append({
            "candidate": name,
            "direction": direction,
            "pore_range": _range(pores),
            "sand_range": _range(sand),
            "separation_ratio": separation,
            "has_positive_gap": separation > 1.0,
        })
    survival_pores = [row["pore_rms_survival"][1] for row in rows if row["truth"] == "pores"]
    survival_sand = [row["pore_rms_survival"][1] for row in rows if row["truth"] == "digital_sand"]
    compared.append({
        "candidate": "survival_existing_baseline",
        "direction": "higher_is_pore_like",
        "pore_range": _range(survival_pores),
        "sand_range": _range(survival_sand),
        "separation_ratio": min(survival_pores) / max(survival_sand),
        "has_positive_gap": False,
    })
    return {
        "selected": "component_density_per_10k",
        "selection_reason": (
            "Largest measured class-bound ratio among the five requested candidates; "
            "the threshold is then derived from core controls only."
        ),
        "rows": compared,
    }


def _markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Skin shape metric: blind human calibration",
        "",
        "## Result",
        "",
        f"- Human/truth: **{summary['human_vs_truth']}/{summary['truth_total']}**.",
        f"- Old metric/human: **{summary['old_vs_human']}/{summary['truth_total']}**.",
        f"- New metric/human: **{summary['new_vs_human']}/{summary['truth_total']}**.",
        f"- New metric/constructive truth: **{summary['new_vs_truth']}/{summary['truth_total']}**.",
        f"- Corrected crops: **{', '.join(map(str, summary['corrected_crops']))}**.",
        "",
        "## Truth / human / old / new",
        "",
        "| crop | case | truth | human | old | new | pore RMS | survival | components/10k | width px |",
        "|---:|---|---|---|---|---|---:|---:|---:|---:|",
    ]
    for row in report["rows"]:
        shape = row["shape"]
        lines.append(
            f"| {row['number']} | {row['case_id']} | {row['truth']} | {row['human']} | "
            f"{row['metric_old']} | {row['metric_new']} | {row['pore_rms_survival'][0]:.4f} | "
            f"{row['pore_rms_survival'][1]:.4f} | {shape['component_density_per_10k']:.3f} | "
            f"{shape['median_component_width_px']:.3f} |"
        )
    lines.extend([
        "",
        "## Candidate comparison",
        "",
        "A ratio above 1 has a positive class-bound gap; below 1 means overlap.",
        "",
        "| candidate | direction | pore range | sand range | separation ratio |",
        "|---|---|---|---|---:|",
    ])
    for row in report["candidate_comparison"]["rows"]:
        lines.append(
            f"| {row['candidate']} | {row['direction']} | "
            f"{row['pore_range'][0]:.4f}..{row['pore_range'][1]:.4f} | "
            f"{row['sand_range'][0]:.4f}..{row['sand_range'][1]:.4f} | "
            f"{row['separation_ratio']:.4f} |"
        )
    calibration = report["calibration"]
    basis = calibration["basis"]
    lines.extend([
        "",
        "## Threshold derivation",
        "",
        f"- Core controls only: {basis['core_control_count']} cases.",
        f"- Core pore maximum density: {basis['core_pore_component_density_max']:.6f}.",
        f"- Core sand minimum density: {basis['core_sand_component_density_min']:.6f}.",
        f"- Sparse maximum = midpoint: **{calibration['shape_max_component_density']:.6f}**.",
        f"- Minimum density and activity guards: **{calibration['shape_min_component_density']:.6f}**, "
        f"**{calibration['shape_min_pore_rms']:.6f}** pore RMS.",
        "- The four fine-pore failures were not used to choose the maximum density threshold.",
        "",
        "## Limits",
        "",
        "The 20 crops are a calibration/regression set, not an independent generalisation test. "
        "Temporal correlation is unregistered and can fall during real motion. A `pores` verdict "
        "is metric evidence, not biological proof; before/after rollback still uses the added delta.",
        "",
    ])
    return "\n".join(lines)


def build_blind_report(blind_root: str | Path) -> dict:
    root = Path(blind_root)
    key = _read_json(root / "blind_key.json")
    human_payload = _read_json(root / "human_labels.json")
    if not human_payload.get("is_human"):
        raise ValueError("human_labels.json must explicitly have is_human=true")
    human_labels = {int(number): value for number, value in human_payload["labels"].items()}

    rows = []
    for crop in key["crops"]:
        number = int(crop["number"])
        human = human_labels[number]
        if isinstance(human, dict):
            human = human["class"]
        image_path = root / crop["crop"]
        measured = measure_image(image_path)
        gray = np.asarray(Image.open(image_path).convert("L"), dtype=np.float64)
        shape = measure_shape(gray)
        old = legacy_classify(measured)
        rows.append({
            "number": number,
            "case_id": crop["case_id"],
            "family": crop["family"],
            "crop": crop["crop"],
            "truth": crop["expected_class"],
            "human": human,
            "metric_old": old,
            "metric_new": measured["classification"],
            "pore_rms_survival": [measured["pore_rms"], measured["survival"]],
            "shape": shape,
        })

    core = [row for row in rows if row["case_id"] in CORE_CASE_IDS]
    core_pores = [row for row in core if row["truth"] == "pores"]
    core_sand = [row for row in core if row["truth"] == "digital_sand"]
    core_plastic = [row for row in core if row["truth"] == "plastic"]
    basis = {
        "core_control_count": len(core),
        "core_pore_component_density_max": max(
            row["shape"]["component_density_per_10k"] for row in core_pores
        ),
        "core_sand_component_density_min": min(
            row["shape"]["component_density_per_10k"] for row in core_sand
        ),
        "core_plastic_pore_rms_max": max(row["pore_rms_survival"][0] for row in core_plastic),
        "core_pore_pore_rms_min": min(row["pore_rms_survival"][0] for row in core_pores),
    }
    summary = {
        "truth_total": len(rows),
        "human_is_ground_truth": all(row["human"] == row["truth"] for row in rows),
        "human_vs_truth": sum(row["human"] == row["truth"] for row in rows),
        "old_vs_human": sum(row["metric_old"] == row["human"] for row in rows),
        "old_vs_truth": sum(row["metric_old"] == row["truth"] for row in rows),
        "new_vs_human": sum(row["metric_new"] == row["human"] for row in rows),
        "new_vs_truth": sum(row["metric_new"] == row["truth"] for row in rows),
        "corrected_crops": [
            row["number"] for row in rows
            if row["metric_old"] != row["metric_new"] and row["metric_new"] == row["human"]
        ],
        "remaining_mismatches": [
            row["number"] for row in rows if row["metric_new"] != row["human"]
        ],
    }
    return {
        "tool": "scripts.skin_metric_shape.calibration",
        "source": root.name,
        "summary": summary,
        "rows": rows,
        "candidate_comparison": _candidate_comparison(rows),
        "calibration": {
            "shape_max_component_density": SHAPE_MAX_COMPONENT_DENSITY,
            "shape_min_component_density": SHAPE_MIN_COMPONENT_DENSITY,
            "shape_min_pore_rms": SHAPE_MIN_PORE_RMS,
            "basis": basis,
        },
    }


def write_blind_report(blind_root: str | Path, out_dir: str | Path) -> tuple[Path, Path]:
    report = build_blind_report(blind_root)
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "blind_calibration.json"
    markdown_path = output / "blind_calibration.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return json_path, markdown_path
