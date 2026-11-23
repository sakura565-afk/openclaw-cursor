"""End-to-end: task.json → маска → ComfyUI inpaint → QA + коллаж."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .comfy_client import ComfyUIClient, ComfyUIError, WorkflowChoice, pick_workflow
from .crop_mode import compute_tiles, prepare_crop, restore_crop
from PIL import Image

from .config import resolve_profile
from .mask import MaskBuildResult, build_mask
from .paste_back import paste_back
from .qa import QAThresholds, evaluate_acceptance, write_qa_artifacts
from .triptych import build_triptych, describe_task


@dataclass
class FurniturePipelineConfig:
    task_path: Path
    output_dir: Path
    dry_run: bool = False
    skip_comfy: bool = False
    qa_only: bool = False
    timeout_sec: float = 900.0


def _load_task(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _mask_spec(task: dict[str, Any]) -> dict[str, Any]:
    return dict(task.get("mask") or task.get("mask_source") or {})


def run_mask_stage(task: dict[str, Any], output_dir: Path) -> MaskBuildResult:
    image = Path(task["reference_image"])
    if not image.is_file():
        raise FileNotFoundError(f"reference_image не найден: {image}")
    spec = _mask_spec(task)
    task_id = str(task.get("id", "furniture_task"))
    mask_file = Path(spec["file"]) if spec.get("file") else None
    bbox = tuple(spec["bbox"]) if spec.get("bbox") else None
    if bbox is not None:
        bbox = (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))
    target = tuple(spec["target_rgb"]) if spec.get("target_rgb") else None
    if target is not None:
        target = (int(target[0]), int(target[1]), int(target[2]))
    return build_mask(
        image,
        output_dir / "masks",
        mask_file=mask_file,
        bbox=bbox,
        feather=float(spec.get("feather", 8.0)),
        target_rgb=target,
        color_tolerance=float(spec.get("color_tolerance", 28.0)),
        color_feather=float(spec.get("color_feather", 4.0)),
        task_id=task_id,
    )



def _crop_mask_file(mask_path: Path, box: list[int], output_dir: Path) -> Path:
    """Обрезать маску под тот же кроп, что и исходник."""
    from PIL import Image

    with Image.open(mask_path) as im:
        m = im.convert("L")
    x0, y0, x1, y1 = box
    cropped = m.crop((x0, y0, x1, y1))
    out = output_dir / (mask_path.stem + "_cropped.png")
    cropped.save(out)
    return out


def run_comfy_stage(
    task: dict[str, Any],
    reference_image: Path,
    mask_result: MaskBuildResult,
    output_dir: Path,
    *,
    timeout_sec: float,
) -> Path:
    """Отрендерить и вернуть ПОЛНЫЙ кадр результата.

    Порядок (проверен на реальных фото, замеры в crop_mode.py):
      1. нативные кропы 768x768 (рецепт-эталон из ТЗ, без апскейла);
         если область шире 768 — режется на перекрывающиеся тайлы;
      2. СПЛОШНАЯ маска на весь кроп — fill-модель перекрашивает только
         при цельной большой области (на дробной маске не перекрашивает);
      3. рендер каждого тайла;
      4. paste-back по ТОЧНОЙ маске ткани внутри тайла — вклеивает
         сгенерированное только в пиксели обивки (подушки/ножки/стол не тронуты);
      5. возврат тайлов в полный кадр.
    """
    profile = resolve_profile(str(task.get("comfy_host", task.get("host", "local"))))
    client = ComfyUIClient(profile.comfy_url, timeout_sec=min(timeout_sec, 180.0))

    crop_spec = task.get("crop") or {}
    target = int(crop_spec.get("target", 768))
    bbox = tuple(crop_spec["bbox"]) if crop_spec.get("bbox") else None
    if bbox is None:
        import numpy as np

        arr = np.asarray(Image.open(mask_result.path).convert("L"))
        ys, xs = np.nonzero(arr > 127)
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if xs.size else (0, 0, 1, 1)
    with Image.open(reference_image) as im:
        ref_size = im.size
    tiles = compute_tiles(bbox, ref_size, target, overlap=int(crop_spec.get("overlap", 96)))

    task_id = str(task.get("id", "src"))
    full_result = output_dir / "comfy_result_full.png"
    full_result = reference_image  # база для вклейки тайлов
    tiles_meta = []

    for idx, box in enumerate(tiles):
        info = prepare_crop(
            reference_image,
            mask_result.path,
            output_dir,
            target=target,
            bbox=box,
            stem=f"tile{idx}",
        )
        src_file = Path(info["ref"])
        mask_file = Path(info["mask"])

        # СПЛОШНАЯ fill-маска на весь кроп: fill-модели нужна цельная область.
        fill_file = output_dir / f"tile{idx}_fill.png"
        Image.new("L", Image.open(src_file).size, 255).save(fill_file)

        src_name = f"furniture_{task_id}_ref{idx}.png"
        mask_name = f"furniture_{task_id}_mask{idx}.png"
        client.upload_image(src_file.read_bytes(), src_name)

        choice: WorkflowChoice = pick_workflow(
            task,
            profile,
            src_name=src_name,
            mask_name=mask_name,
            positive=task.get("prompt"),
            seed=task.get("seed"),
        )
        # Маска на генерацию выбирается явно:
        #   "solid"   — весь кроп белый. Нужно fill-моделям (Flux Fill) и,
        #                судя по замерам, SDXL-инпейнту с дробной маской:
        #                точные границы всё равно ставит paste-back.
        #   "precise" — точная маска ткани (по умолчанию для SDXL).
        #   "auto"    — solid для fill-моделей, precise для остальных.
        gen_mode = str(task.get("gen_mask", "auto")).lower()
        if gen_mode == "auto":
            gen_mode = "solid" if choice.kind == "flux_fill_gguf" else "precise"
        gen_mask = fill_file if gen_mode == "solid" else mask_file
        client.upload_image(Path(gen_mask).read_bytes(), mask_name)
        if idx == 0:
            (output_dir / "comfy_workflow_choice.json").write_text(
                json.dumps(
                    {"kind": choice.kind, "workflow_file": choice.workflow_file, "notes": choice.notes},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        prompt_id = client.submit_workflow(choice.prompt)
        history = client.wait_for_result(prompt_id, timeout_sec=timeout_sec)
        ref_img = client.first_output_image(history)
        if not ref_img:
            raise ComfyUIError(f"В history нет output images: {history}")
        data = client.download_view(ref_img)
        raw_path = output_dir / f"comfy_result_raw_{idx}.png"
        raw_path.write_bytes(data)
        (output_dir / "comfy_prompt_id.txt").write_text(prompt_id, encoding="utf-8")

        # Склейка по точной маске ткани ВНУТРИ кропа.
        clean_path = output_dir / f"comfy_result_clean_{idx}.png"
        clean_metrics = paste_back(
            src_file,
            raw_path,
            mask_file,
            clean_path,
            feather=float(task.get("paste_back_feather", 6.0)),
            erode_px=int(task.get("paste_back_erode", 2)),
        )
        tiles_meta.append({**info, "paste_back": clean_metrics, "prompt_id": prompt_id})

        # Вклейка тайла в полный кадр.
        full_result = restore_crop(
            clean_path, full_result if Path(full_result).is_file() else reference_image,
            info["box"], output_dir / f"comfy_result_full_{idx}.png",
        )

    # Накопленный кадр после всех тайлов — это и есть результат.
    final = output_dir / "comfy_result_full.png"
    accumulated = Path(str(full_result))
    if accumulated.exists() and accumulated != final:
        import shutil

        shutil.copy2(accumulated, final)
    (output_dir / "crop_info.json").write_text(
        json.dumps({"tiles": tiles_meta}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return final

def run_qa_stage(
    task: dict[str, Any],
    reference_image: Path,
    result_image: Path,
    mask_result: MaskBuildResult,
    output_dir: Path,
) -> dict[str, Any]:
    collage = output_dir / "reference_result_collage.png"
    sample = task.get("sample_rgb")
    sample_t = tuple(sample) if sample else None
    if sample_t is not None:
        sample_t = (int(sample_t[0]), int(sample_t[1]), int(sample_t[2]))
    th_raw = task.get("qa_thresholds") or {}
    thresholds = QAThresholds(
        max_outside_change_fraction=float(
            th_raw.get("max_outside_change_fraction", QAThresholds.max_outside_change_fraction)
        ),
        min_inside_change_fraction=float(
            th_raw.get("min_inside_change_fraction", QAThresholds.min_inside_change_fraction)
        ),
        min_gradient_correlation=float(
            th_raw.get("min_gradient_correlation", QAThresholds.min_gradient_correlation)
        ),
        max_inside_lab_shift=float(
            th_raw.get("max_inside_lab_shift", QAThresholds.max_inside_lab_shift)
        ),
    )
    report = evaluate_acceptance(
        reference_image,
        result_image,
        mask_result.path,
        collage,
        thresholds=thresholds,
        sample_rgb=sample_t,
    )
    paths = write_qa_artifacts(report, output_dir / "qa", stem="acceptance")
    return {
        "verdict": report.verdict,
        "passed": report.passed,
        "collage": str(report.collage_path),
        "qa_paths": {k: str(v) for k, v in paths.items()},
        "metrics": report.metrics,
        "reasons": report.reasons,
        "text_report": report.text_report,
    }


def run_pipeline(cfg: FurniturePipelineConfig) -> dict[str, Any]:
    task = _load_task(cfg.task_path)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    ref = Path(task["reference_image"])
    summary: dict[str, Any] = {
        "task_id": task.get("id"),
        "dry_run": cfg.dry_run,
        "output_dir": str(cfg.output_dir),
    }

    mask_result = run_mask_stage(task, cfg.output_dir)
    summary["mask"] = mask_result.to_dict()

    if cfg.dry_run:
        profile = resolve_profile(str(task.get("comfy_host", "local")))
        choice = pick_workflow(task, profile)
        summary["workflow_kind"] = choice.kind
        summary["workflow_notes"] = choice.notes
        return summary

    if cfg.qa_only:
        result_path = Path(task["result_image"])
        if not result_path.is_file():
            raise FileNotFoundError("qa_only требует result_image в task.json")
    elif cfg.skip_comfy:
        result_path = Path(task.get("result_image", ref))
        if not result_path.is_file():
            raise FileNotFoundError("skip_comfy: укажите result_image")
    else:
        try:
            result_path = run_comfy_stage(
                task,
                ref,
                mask_result,
                cfg.output_dir,
                timeout_sec=cfg.timeout_sec,
            )
        except ComfyUIError as exc:
            summary["comfy_error"] = str(exc)
            if task.get("result_image"):
                result_path = Path(task["result_image"])
            else:
                raise

    # Paste-back: fill-модели перерисовывают весь кадр, поэтому результат
    # склеивается с референсом по точной маске. При crop-режиме склейка уже
    # выполнена внутри кропа (run_comfy_stage) — здесь только без crop.
    if (
        not task.get("skip_paste_back", False)
        and mask_result.path.is_file()
        and not (task.get("crop") or {})
    ):
        try:
            pb = paste_back(
                ref,
                result_path,
                mask_result.path,
                cfg.output_dir / "comfy_result_pasteback.png",
                feather=float(task.get("paste_back_feather", 6.0)),
                erode_px=int(task.get("paste_back_erode", 2)),
            )
            summary["paste_back"] = pb
            result_path = cfg.output_dir / "comfy_result_pasteback.png"
        except Exception as exc:  # noqa: BLE001
            summary["paste_back_error"] = str(exc)

    summary["result_image"] = str(result_path)
    summary["qa"] = run_qa_stage(task, ref, result_path, mask_result, cfg.output_dir)

    # Триптих приёмки: вход | что делаем | выход
    try:
        choice_kind = str((summary.get("workflow_kind") or "") or "")
        choice_notes = str((summary.get("workflow_notes") or "") or "")
        meta_p = cfg.output_dir / "comfy_workflow_choice.json"
        if meta_p.is_file():
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            choice_kind = str(meta.get("kind") or choice_kind)
            choice_notes = str(meta.get("notes") or choice_notes)
        if not choice_kind:
            choice_kind = "flux_fill"
        desc = describe_task(task, choice_kind, choice_notes)
        metrics = [f"{k}: {round(float(v), 4)}" for k, v in ((summary["qa"].get("metrics") or {}).items())]
        tri = cfg.output_dir / "triptych_input_description_output.png"
        build_triptych(
            ref,
            result_path,
            tri,
            title=f"Мебельный inpaint: {task.get('id', 'task')}",
            description_lines=desc,
            metrics_lines=metrics,
            verdict=str(summary["qa"].get("verdict") or ""),
        )
        summary["triptych"] = str(tri)
    except Exception as exc:  # noqa: BLE001
        summary["triptych_error"] = str(exc)

    report_path = cfg.output_dir / "pipeline_report.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    summary["report_path"] = str(report_path)
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Локальный мебельный inpaint: task.json → маска → ComfyUI → QA-коллаж.",
    )
    parser.add_argument("task", type=Path, nargs="?", help="Путь к task.json")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("artifacts/furniture_local/run"),
        help="Каталог артефактов",
    )
    parser.add_argument("--dry-run", action="store_true", help="Только маска и выбор workflow")
    parser.add_argument("--skip-comfy", action="store_true", help="Пропустить ComfyUI, взять result_image")
    parser.add_argument("--qa-only", action="store_true", help="Только QA + коллаж")
    parser.add_argument("--timeout", type=float, default=900.0, help="Таймаут ожидания ComfyUI, сек")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if not args.task:
        parser.print_help()
        return 0
    cfg = FurniturePipelineConfig(
        task_path=args.task,
        output_dir=args.output,
        dry_run=args.dry_run,
        skip_comfy=args.skip_comfy,
        qa_only=args.qa_only,
        timeout_sec=args.timeout,
    )
    try:
        summary = run_pipeline(cfg)
    except (OSError, ValueError, ComfyUIError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    # пути в сводке - Path, их нужно привести к строке для JSON
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    verdict = (summary.get("qa") or {}).get("verdict")
    if verdict == "FAIL":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())