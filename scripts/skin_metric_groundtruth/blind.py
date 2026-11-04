"""Numbered blind sheets built with the repository's eval_judge sheet code."""

from __future__ import annotations

import json
import random
from pathlib import Path

from PIL import Image, ImageDraw

from scripts.eval_judge import sheet as sheet_mod

DEFAULT_BLIND_SEED = 90210
DEFAULT_COLUMNS = 5


def _judge_prompt(count: int) -> str:
    lines = [
        "You are an external BLIND vision judge of synthetic surface plates.",
        "The source names and constructive labels are deliberately withheld.",
        "Judge only visible texture in blind_sheet.png; ignore the small crop numbers.",
        "For EVERY numbered crop choose exactly one class:",
        "  plastic: smooth, no visible pores and no visible granular noise;",
        "  pores: spatially discrete pore/pit-like marks, not a uniform noise carpet;",
        "  digital_sand: visible isotropic grain/noise, including noise over pits;",
        "  uncertain: the crop cannot be classified confidently at the supplied scale.",
        "Do not infer labels from order, brightness, file names, or this prompt's example.",
        "Synthetic pits are allowed to look artificial; classify structure, not naturalness.",
        "Give confidence high/medium/low and a short visual reason for every crop.",
        "Answer with JSON only, in this shape (replace the example classes):",
        "{",
    ]
    entries = []
    for number in range(1, count + 1):
        entries.append(
            f'  "{number}": {{"class": "plastic", "confidence": "high", '
            f'"reason": "replace after looking"}}'
        )
    lines.append(",\n".join(entries))
    lines.extend(
        [
            "}",
            "Do not add variant names. A human must review these labels before any",
            "threshold is treated as validated; an AI visual pass is only a blind pre-check.",
        ]
    )
    return "\n".join(lines)


def build_blind_set(
    manifest_path: str | Path,
    out_dir: str | Path,
    *,
    seed: int = DEFAULT_BLIND_SEED,
    columns: int = DEFAULT_COLUMNS,
) -> dict:
    manifest_path = Path(manifest_path)
    root = manifest_path.parent
    out_dir = Path(out_dir)
    crop_dir = out_dir / "crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = list(manifest["cases"])
    random.Random(seed).shuffle(cases)
    if not cases:
        raise ValueError("cannot build a blind set without cases")
    if columns < 1:
        raise ValueError("columns must be positive")

    crop_records: list[dict] = []
    crop_paths: list[str] = []
    for number, case in enumerate(cases, start=1):
        crop_relative = Path("crops") / f"crop_{number:02d}.png"
        with Image.open(root / case["image"]) as image:
            image.convert("L").save(out_dir / crop_relative)
        crop_records.append(
            {
                "number": number,
                "case_id": case["case_id"],
                "expected_class": case["expected_class"],
                "family": case["family"],
                "crop": crop_relative.as_posix(),
                "source_image": case["image"],
            }
        )
        crop_paths.append(str(out_dir / crop_relative))

    grouped_paths = [crop_paths[index:index + columns] for index in range(0, len(crop_paths), columns)]
    sheet_path = out_dir / "blind_sheet.png"
    sheet_mod.build_sheet(
        grouped_paths,
        ["" for _ in grouped_paths],
        sheet_path,
        title="",
    )
    with Image.open(sheet_path) as opened:
        sheet = opened.convert("RGB")
    draw = ImageDraw.Draw(sheet)
    for index in range(len(crop_records)):
        row, column = divmod(index, columns)
        left = sheet_mod.MARGIN + sheet_mod.LABEL_WIDTH + column * sheet_mod.CELL_SIZE
        top = sheet_mod.MARGIN + sheet_mod.HEADER_HEIGHT + row * sheet_mod.CELL_SIZE
        draw.rectangle((left + 3, top + 3, left + 33, top + 24), fill=sheet_mod.BACKGROUND)
        draw.text((left + 7, top + 6), f"{index + 1:02d}", fill=sheet_mod.FOREGROUND)
    sheet.save(sheet_path)

    prompt = _judge_prompt(len(crop_records))
    (out_dir / "judge_prompt.txt").write_text(prompt, encoding="utf-8")
    key = {
        "seed": int(seed),
        "withhold_from_judge": True,
        "crops": crop_records,
    }
    (out_dir / "blind_key.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = {
        "tool": "scripts/skin_metric_groundtruth/blind.py",
        "seed": int(seed),
        "columns": int(columns),
        "sheet": sheet_path.name,
        "prompt": "judge_prompt.txt",
        "key": "blind_key.json",
        "crop_count": len(crop_records),
        "visual_status": "pending_external_judge",
        "privacy": "The prompt and numbered crops contain no constructive case identifiers.",
    }
    (out_dir / "blind_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
