"""Calibration tables without silently fitting thresholds to their own labels."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.skin_texture_metrics import measure_image
from scripts.skin_texture_metrics.metrics import (
    PLASTIC_MAX_PORE_RMS,
    SAND_MAX_SURVIVAL,
)

METRIC_KEYS = (
    "pore",
    "pore_blur",
    "spec",
    "pore_ratio",
    "pore_rms",
    "survival",
    "structure_score",
    "lap",
    "classification",
)
VISUAL_CLASSES = {"plastic", "pores", "digital_sand", "uncertain"}


def _read_visual_labels(path: Path | None) -> tuple[dict[int, dict], dict]:
    if path is None:
        return {}, {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_labels = payload.get("labels", payload)
    if not isinstance(raw_labels, dict):
        raise ValueError("visual labels must be a JSON object or contain a labels object")
    labels: dict[int, dict] = {}
    for raw_number, raw_value in raw_labels.items():
        number = int(raw_number)
        value = {"class": raw_value} if isinstance(raw_value, str) else dict(raw_value)
        visual_class = str(value.get("class", ""))
        if visual_class not in VISUAL_CLASSES:
            raise ValueError(f"unsupported visual class {visual_class!r} for crop {number}")
        value["class"] = visual_class
        labels[number] = value
    return labels, payload


def _threshold_recommendation(rows: list[dict]) -> dict:
    core = [row for row in rows if row["core"]]
    fine_failures = [
        row["case_id"]
        for row in rows
        if row["family"] == "fine_pores" and not row["truth_match"]
    ]
    core_pores = [float(row["metrics"]["pore_rms"]) for row in core if row["constructive_label"] == "pores"]
    core_plastic = [float(row["metrics"]["pore_rms"]) for row in core if row["constructive_label"] == "plastic"]
    core_sand_survival = [
        float(row["metrics"]["survival"]) for row in core if row["constructive_label"] == "digital_sand"
    ]
    core_pore_survival = [
        float(row["metrics"]["survival"]) for row in core if row["constructive_label"] == "pores"
    ]
    fine_pore_survival = [
        float(row["metrics"]["survival"]) for row in rows if row["family"] == "fine_pores"
    ]
    mixed_survival = [
        float(row["metrics"]["survival"]) for row in rows if row["family"] == "mixed"
    ]
    survival_overlap = bool(
        fine_pore_survival
        and mixed_survival
        and min(fine_pore_survival) <= max(mixed_survival)
        and min(mixed_survival) <= max(fine_pore_survival)
    )
    return {
        "status": "provisional_pending_human_review",
        "plastic_max_pore_rms": float(PLASTIC_MAX_PORE_RMS),
        "sand_max_survival": float(SAND_MAX_SURVIVAL),
        "action": "retain existing values provisionally; do not auto-fit or claim validation",
        "basis": {
            "core_plastic_pore_rms_max": max(core_plastic, default=None),
            "core_pores_pore_rms_min": min(core_pores, default=None),
            "core_sand_survival_max": max(core_sand_survival, default=None),
            "core_pores_survival_min": min(core_pore_survival, default=None),
            "fine_pore_survival_range": [
                min(fine_pore_survival, default=None),
                max(fine_pore_survival, default=None),
            ],
            "mixed_survival_range": [
                min(mixed_survival, default=None),
                max(mixed_survival, default=None),
            ],
            "fine_pore_and_mixed_survival_ranges_overlap": survival_overlap,
            "fine_pore_truth_mismatches": fine_failures,
        },
        "justification": (
            "The retained thresholds separate the four constructive core controls. "
            "The normal fine-pore diagnostics test a different spatial scale and expose "
            "whether energy plus blur survival is sufficient; failures are reported, not "
            "used to move a threshold until a blind human labels the same sheets."
        ),
        "caveat": (
            "Final visual judgement remains with a human. Synthetic labels establish what "
            "was constructed, not what a human sees; AI visual labels are only a pre-check."
        ),
    }


def build_calibration_report(
    manifest_path: str | Path,
    *,
    blind_key_path: str | Path | None = None,
    visual_labels_path: str | Path | None = None,
) -> dict:
    manifest_path = Path(manifest_path)
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    key_by_case: dict[str, int] = {}
    if blind_key_path is not None:
        key = json.loads(Path(blind_key_path).read_text(encoding="utf-8"))
        key_by_case = {crop["case_id"]: int(crop["number"]) for crop in key["crops"]}
    visual_labels, visual_payload = _read_visual_labels(
        Path(visual_labels_path) if visual_labels_path is not None else None
    )

    rows: list[dict] = []
    for case in manifest["cases"]:
        measured = measure_image(root / case["image"])
        crop_number = key_by_case.get(case["case_id"])
        visual = visual_labels.get(crop_number) if crop_number is not None else None
        visual_class = visual.get("class") if visual else None
        actual_class = str(measured["classification"])
        row = {
            "case_id": case["case_id"],
            "family": case["family"],
            "core": bool(case["core"]),
            "constructive_label": case["expected_class"],
            "metrics": {name: measured[name] for name in METRIC_KEYS},
            "metric_class": actual_class,
            "truth_match": actual_class == case["expected_class"],
            "parameters": case["parameters"],
            "image": case["image"],
            "blind_crop": crop_number,
            "visual_label": visual,
            "metric_matches_visual": (
                actual_class == visual_class if visual_class in EXPECTED_VISUAL_CLASSES else None
            ),
            "visual_matches_constructive_truth": (
                visual_class == case["expected_class"]
                if visual_class in EXPECTED_VISUAL_CLASSES
                else None
            ),
        }
        rows.append(row)

    core = [row for row in rows if row["core"]]
    visual_rows = [row for row in rows if row["visual_label"] is not None]
    truth_mismatches = [row["case_id"] for row in rows if not row["truth_match"]]
    visual_mismatches = [
        row["case_id"] for row in visual_rows if row["metric_matches_visual"] is False
    ]
    summary = {
        "total": len(rows),
        "truth_matches": sum(bool(row["truth_match"]) for row in rows),
        "truth_mismatches": truth_mismatches,
        "core_total": len(core),
        "core_truth_matches": sum(bool(row["truth_match"]) for row in core),
        "visual_total": len(visual_rows),
        "visual_metric_matches": sum(row["metric_matches_visual"] is True for row in visual_rows),
        "visual_metric_mismatches": visual_mismatches,
        "visual_truth_matches": sum(
            row["visual_matches_constructive_truth"] is True for row in visual_rows
        ),
        "by_family": {
            family: {
                "total": len(family_rows),
                "truth_matches": sum(bool(row["truth_match"]) for row in family_rows),
            }
            for family in sorted({row["family"] for row in rows})
            for family_rows in [[row for row in rows if row["family"] == family]]
        },
    }
    return {
        "tool": "scripts/skin_metric_groundtruth/calibration.py",
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "manifest": manifest_path.name,
        "summary": summary,
        "rows": rows,
        "visual_assessment": {
            "status": "labels_loaded" if visual_labels else "pending_external_judge",
            "judge": visual_payload.get("judge"),
            "is_human": bool(visual_payload.get("is_human", False)),
            "source_file": Path(visual_labels_path).name if visual_labels_path else None,
            "note": "Constructive labels and visual labels are kept as separate columns.",
        },
        "recommended_thresholds": _threshold_recommendation(rows),
    }


EXPECTED_VISUAL_CLASSES = {"plastic", "pores", "digital_sand"}


def calibration_markdown(report: dict) -> str:
    lines = [
        "# Skin-metric independent calibration",
        "",
        "Constructive truth is not human eye labelling. Disagreements are retained.",
        "",
        "## Summary",
        "",
        f"- Core constructive controls: {report['summary']['core_truth_matches']}/"
        f"{report['summary']['core_total']} metric matches.",
        f"- All synthetic cases: {report['summary']['truth_matches']}/"
        f"{report['summary']['total']} constructive-label matches.",
        f"- Blind visual labels loaded: {report['summary']['visual_total']}; "
        f"metric/visual matches: {report['summary']['visual_metric_matches']}.",
        f"- Constructive mismatches: {', '.join(report['summary']['truth_mismatches']) or 'none'}.",
        f"- Visual/metric mismatches: {', '.join(report['summary']['visual_metric_mismatches']) or 'none loaded'}.",
        "",
        "## Label -> metric -> class",
        "",
        "| case | blind crop | constructive | visual | pore_rms | survival | metric | truth match |",
        "|---|---:|---|---|---:|---:|---|---|",
    ]
    for row in report["rows"]:
        visual = row["visual_label"] or {}
        lines.append(
            f"| {row['case_id']} | {row['blind_crop'] or ''} | {row['constructive_label']} | "
            f"{visual.get('class', 'pending')} | {float(row['metrics']['pore_rms']):.4f} | "
            f"{float(row['metrics']['survival']):.4f} | {row['metric_class']} | "
            f"{row['truth_match']} |"
        )
    recommendation = report["recommended_thresholds"]
    lines.extend(
        [
            "",
            "## Provisional thresholds",
            "",
            f"- plastic maximum pore RMS: **{recommendation['plastic_max_pore_rms']:.3f}**",
            f"- digital-sand maximum survival: **{recommendation['sand_max_survival']:.3f}**",
            f"- action: {recommendation['action']}",
            f"- rationale: {recommendation['justification']}",
            f"- caveat: {recommendation['caveat']}",
            "",
        ]
    )
    return "\n".join(lines)


def write_calibration_report(report: dict, out_dir: str | Path) -> tuple[Path, Path]:
    root = Path(out_dir)
    json_path = root / "calibration_report.json"
    markdown_path = root / "calibration_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(calibration_markdown(report), encoding="utf-8")
    return json_path, markdown_path
