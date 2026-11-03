"""Sequential same-seed A/B benchmark for MiniMax H3 LoRAs.

The baseline uses the identical prompt (including trigger), seed and sampling recipe.
Metrics are fixed-ROI proxies; face_mae is not a face-recognition embedding.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.skin_mask_v2 import skin_mask_v2


@dataclass(frozen=True)
class LoraSpec:
    name: str
    strength: float


@dataclass(frozen=True)
class RenderSpec:
    lora_name: str
    strength: float
    reference: str
    prompt_file: Path
    audio: Path
    output: Path
    prefix: str
    runner: Path
    width: int = 640
    height: int = 384
    length: int = 73
    steps: int = 16
    seed: int = 41001
    wait: int = 840
    prompt_prefix: str = ""


@dataclass
class BenchConfig:
    reference: Path
    prompt_file: Path
    audio: Path
    output_dir: Path
    loras: list[LoraSpec]
    runner: Path
    reference_name: str = ""
    seed: int = 41001
    width: int = 640
    height: int = 384
    length: int = 73
    steps: int = 16
    prompt_prefix: str = ""
    face_roi: tuple[float, float, float, float] | None = None
    skin_roi: tuple[float, float, float, float] | None = None
    no_face: bool = False
    comfy_url: str = "http://127.0.0.1:8188"
    queue_timeout_s: float = 900.0
    render_timeout_s: float = 900.0
    render_wait_s: int = 840
    extra: dict = field(default_factory=dict)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:100] or "lora"


def render_command(spec: RenderSpec) -> list[str]:
    loras = [] if not spec.lora_name else [{"n": spec.lora_name, "sm": spec.strength, "sc": spec.strength}]
    command = [
        sys.executable, str(spec.runner), "--image", spec.reference,
        "--prompt-file", str(spec.prompt_file), "--loras-json", json.dumps(loras, ensure_ascii=False),
        "--width", str(spec.width), "--height", str(spec.height), "--length", str(spec.length),
        "--steps", str(spec.steps), "--seed", str(spec.seed), "--prefix", spec.prefix,
        "--audio", str(spec.audio), "--output", str(spec.output), "--wait", str(spec.wait),
    ]
    if spec.prompt_prefix:
        command.extend(["--prompt-prefix", spec.prompt_prefix])
    return command


def subprocess_render(spec: RenderSpec, timeout_s: float = 900.0) -> None:
    if timeout_s > 900:
        raise ValueError("each render is capped at 900 seconds (15 minutes)")
    try:
        completed = subprocess.run(
            render_command(spec), capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"render exceeded the 15-minute/900s wall-clock cap: {spec.output}") from exc
    if completed.returncode:
        raise RuntimeError(
            f"renderer exited {completed.returncode}: {(completed.stderr or completed.stdout)[-2000:]}"
        )
    if not spec.output.is_file() or spec.output.stat().st_size == 0:
        raise RuntimeError(f"renderer returned success but output is missing: {spec.output}")


def wait_for_comfy(*, base_url: str, timeout_s: float, poll_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with urllib.request.urlopen(base_url.rstrip("/") + "/queue", timeout=10) as response:
                queue = json.loads(response.read().decode("utf-8"))
            if not queue.get("queue_running") and not queue.get("queue_pending"):
                return
        except (OSError, ValueError) as exc:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"ComfyUI queue unavailable: {exc}") from exc
        if time.monotonic() >= deadline:
            raise TimeoutError(f"ComfyUI stayed busy for {timeout_s:g}s")
        time.sleep(min(poll_s, max(0.0, deadline - time.monotonic())))


def read_video(path: str | Path) -> list[np.ndarray]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"cannot open video: {path}")
    frames = []
    try:
        while True:
            ok, bgr = capture.read()
            if not ok:
                break
            frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    finally:
        capture.release()
    if not frames:
        raise ValueError(f"video has no decodable frames: {path}")
    return frames


def read_rgb(path: str | Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"cannot decode reference image: {path}")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def detect_face_roi(image: np.ndarray) -> tuple[float, float, float, float] | None:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(str(cascade_path))
    faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(32, 32))
    if not len(faces):
        return None
    x, y, w, h = max(faces, key=lambda box: int(box[2]) * int(box[3]))
    height, width = gray.shape
    return (float(x / width), float(y / height), float(w / width), float(h / height))


def crop_roi(image: np.ndarray, roi: tuple[float, float, float, float]) -> np.ndarray:
    height, width = image.shape[:2]
    x, y, w, h = roi
    x1, y1 = max(0, round(x * width)), max(0, round(y * height))
    x2, y2 = min(width, round((x + w) * width)), min(height, round((y + h) * height))
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"invalid normalized ROI: {roi}")
    return image[y1:y2, x1:x2]


def _gray_laplacian(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    return gray, cv2.Laplacian(gray, cv2.CV_32F, ksize=3)


def _frame_descriptor(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    grid = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA).astype(np.float32)
    hash_grid = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    return grid, (hash_grid[:, 1:] > hash_grid[:, :-1]).reshape(-1)


def measure_frames(
    frames: list[np.ndarray],
    *,
    reference: np.ndarray,
    face_roi: tuple[float, float, float, float] | None,
    skin_mask: np.ndarray | None,
    parked_grid_threshold: float = 0.25,
    parked_hash_threshold: int = 1,
) -> dict:
    if not frames:
        raise ValueError("at least one frame is required")
    shape = frames[0].shape[:2]
    if any(frame.shape[:2] != shape for frame in frames):
        raise ValueError("all frames must have the same dimensions")
    reference = cv2.resize(reference, (shape[1], shape[0]), interpolation=cv2.INTER_AREA)
    mask = None
    if skin_mask is not None:
        mask = cv2.resize(skin_mask, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST).astype(np.float32)
        if float(mask.sum()) <= 1.0:
            mask = None

    whole_texture, skin_texture, face_errors = [], [], []
    grays, laps, descriptors = [], [], []
    for frame in frames:
        gray, lap = _gray_laplacian(frame)
        grays.append(gray)
        laps.append(lap)
        descriptors.append(_frame_descriptor(gray))
        whole_texture.append(float(np.mean(np.abs(lap))))
        if mask is not None:
            skin_texture.append(float(np.sum(np.abs(lap) * mask) / np.sum(mask)))
        if face_roi is not None:
            candidate_face = crop_roi(frame, face_roi).astype(np.float32)
            reference_face = crop_roi(reference, face_roi).astype(np.float32)
            face_errors.append(float(np.mean(np.abs(candidate_face - reference_face))))

    rgb_deltas, hf_deltas, skin_hf_deltas = [], [], []
    parked_pairs, parked_run, parked_max_run = 0, 1, 1
    for index in range(1, len(frames)):
        rgb_deltas.append(float(np.mean(np.abs(frames[index].astype(np.float32) - frames[index - 1]))))
        hf_deltas.append(float(np.mean(np.abs(laps[index] - laps[index - 1]))))
        if mask is not None:
            skin_hf_deltas.append(float(np.sum(np.abs(laps[index] - laps[index - 1]) * mask) / np.sum(mask)))
        previous_grid, previous_hash = descriptors[index - 1]
        current_grid, current_hash = descriptors[index]
        parked = (
            float(np.mean(np.abs(current_grid - previous_grid))) <= parked_grid_threshold
            and int(np.count_nonzero(current_hash != previous_hash)) <= parked_hash_threshold
        )
        if parked:
            parked_pairs += 1
            parked_run += 1
            parked_max_run = max(parked_max_run, parked_run)
        else:
            parked_run = 1

    return {
        "frame_count": len(frames),
        "microtexture_whole": float(np.mean(whole_texture)),
        "microtexture_skin": float(np.mean(skin_texture)) if skin_texture else None,
        "temporal_rgb_delta": float(np.mean(rgb_deltas)) if rgb_deltas else 0.0,
        "temporal_hf_delta": float(np.mean(hf_deltas)) if hf_deltas else 0.0,
        "temporal_hf_skin_delta": float(np.mean(skin_hf_deltas)) if skin_hf_deltas else None,
        "face_mae": float(np.mean(face_errors)) if face_errors else None,
        "parked_frame_pairs": parked_pairs,
        "parked_max_run": parked_max_run,
        "skin_mask_coverage": float(np.mean(mask >= 0.5)) if mask is not None else 0.0,
    }


def measure_video(
    path: str | Path,
    *,
    reference: str | Path,
    face_roi: tuple[float, float, float, float] | None = None,
    skin_roi: tuple[float, float, float, float] | None = None,
    no_face: bool = False,
) -> dict:
    reference_image = read_rgb(reference)
    frames = read_video(path)
    if not no_face and face_roi is None:
        face_roi = detect_face_roi(reference_image)
    skin_source = crop_roi(reference_image, skin_roi) if skin_roi else reference_image
    mask = skin_mask_v2(skin_source, use_comfy=False)
    if skin_roi:
        full_mask = np.zeros(reference_image.shape[:2], dtype=np.float32)
        height, width = reference_image.shape[:2]
        x, y, w, h = skin_roi
        x1, y1, x2, y2 = round(x * width), round(y * height), round((x + w) * width), round((y + h) * height)
        full_mask[y1:y2, x1:x2] = mask
        mask = full_mask
    return measure_frames(frames, reference=reference_image, face_roi=face_roi, skin_mask=mask)


def percent_change(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None or baseline == 0:
        return None
    return 100.0 * (candidate - baseline) / abs(baseline)


def compare_to_baseline(candidate: dict, baseline: dict) -> dict:
    comparison = {
        "microtexture_whole_delta_pct": percent_change(candidate.get("microtexture_whole"), baseline.get("microtexture_whole")),
        "microtexture_skin_delta_pct": percent_change(candidate.get("microtexture_skin"), baseline.get("microtexture_skin")),
        "temporal_hf_delta_pct": percent_change(candidate.get("temporal_hf_delta"), baseline.get("temporal_hf_delta")),
        "face_mae_delta_pct": percent_change(candidate.get("face_mae"), baseline.get("face_mae")),
        "parked_pairs_delta": candidate.get("parked_frame_pairs", 0) - baseline.get("parked_frame_pairs", 0),
    }
    skin_delta = comparison["microtexture_skin_delta_pct"]
    whole_delta = comparison["microtexture_whole_delta_pct"]
    texture_delta = skin_delta if skin_delta is not None else whole_delta
    hf_delta = comparison["temporal_hf_delta_pct"]
    face_delta = comparison["face_mae_delta_pct"]
    hf_from_zero = baseline.get("temporal_hf_delta") == 0 and candidate.get("temporal_hf_delta", 0) > 0
    face_from_zero = baseline.get("face_mae") == 0 and candidate.get("face_mae", 0) > 0
    texture_from_zero = (
        skin_delta is None and whole_delta is None
        and (baseline.get("microtexture_skin") if baseline.get("microtexture_skin") is not None else baseline.get("microtexture_whole")) == 0
        and (candidate.get("microtexture_skin") if candidate.get("microtexture_skin") is not None else candidate.get("microtexture_whole", 0)) > 0
    )
    hf_regress = (hf_delta is not None and hf_delta > 10.0) or hf_from_zero
    if hf_regress:
        verdict = "reject"
    elif (face_delta is not None and face_delta > 10.0) or face_from_zero or comparison["parked_pairs_delta"] > 0:
        verdict = "reject"
    elif (texture_delta is not None and texture_delta >= 5.0) or texture_from_zero:
        verdict = "candidate"
    elif texture_delta is None or hf_delta is None:
        verdict = "inconclusive"
    else:
        verdict = "no_gain"
    comparison["verdict"] = verdict
    return comparison


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def run_bench(config: BenchConfig, *, render_fn=subprocess_render, measure_fn=measure_video, wait_fn=wait_for_comfy) -> dict:
    if config.render_timeout_s > 900:
        raise ValueError("render_timeout_s must not exceed 900 seconds")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    journal = config.output_dir / "results.jsonl"
    journal.write_text("", encoding="utf-8")
    specs = [LoraSpec("", 0.0)] + config.loras
    records = []
    baseline_metrics = None
    for index, lora in enumerate(specs):
        label = "baseline" if not lora.name else f"{slugify(Path(lora.name).stem)}-{lora.strength:g}"
        output = config.output_dir / f"{index:02d}-{label}.mp4"
        prefix = f"h3_lora_bench_{config.seed}_{index:02d}_{label}"
        spec = RenderSpec(
            lora_name=lora.name, strength=lora.strength,
            reference=config.reference_name or config.reference.name,
            prompt_file=config.prompt_file, audio=config.audio, output=output, prefix=prefix,
            runner=config.runner, width=config.width, height=config.height, length=config.length,
            steps=config.steps, seed=config.seed, wait=config.render_wait_s,
            prompt_prefix=config.prompt_prefix,
        )
        record = {"label": label, "lora_name": lora.name, "strength": lora.strength, "seed": config.seed, "output": str(output)}
        started = time.monotonic()
        try:
            wait_fn(base_url=config.comfy_url, timeout_s=config.queue_timeout_s)
            render_fn(spec, timeout_s=config.render_timeout_s)
            metrics = measure_fn(
                output, reference=config.reference, face_roi=config.face_roi,
                skin_roi=config.skin_roi, no_face=config.no_face,
            )
            record.update({"status": "ok", "metrics": metrics})
            if index == 0:
                baseline_metrics = metrics
            else:
                record["comparison"] = compare_to_baseline(metrics, baseline_metrics)
        except Exception as exc:
            record.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
            if index == 0:
                records.append(record)
                with journal.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                raise RuntimeError("baseline failed; candidate comparisons would be meaningless") from exc
        record["elapsed_s"] = round(time.monotonic() - started, 3)
        records.append(record)
        with journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = {
        "schema_version": 1,
        "config": {**asdict(config), "reference": str(config.reference), "prompt_file": str(config.prompt_file),
                   "audio": str(config.audio), "output_dir": str(config.output_dir), "runner": str(config.runner),
                   "loras": [asdict(lora) for lora in config.loras]},
        "renders": records,
    }
    _write_json(config.output_dir / "summary.json", summary)
    with (config.output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        fields = ["label", "status", "microtexture_whole", "microtexture_skin", "temporal_rgb_delta",
                  "temporal_hf_delta", "face_mae", "parked_frame_pairs", "texture_delta_pct", "verdict"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in records:
            metrics = record.get("metrics", {})
            comparison = record.get("comparison", {})
            writer.writerow({
                "label": record["label"], "status": record["status"],
                **{name: metrics.get(name) for name in fields[2:8]},
                "texture_delta_pct": comparison.get("microtexture_skin_delta_pct", comparison.get("microtexture_whole_delta_pct")),
                "verdict": comparison.get("verdict", "baseline" if record["label"] == "baseline" else "error"),
            })
    return summary


def parse_lora(value: str, catalog_rows: list[dict]) -> LoraSpec:
    name, separator, strength_text = value.rpartition("@")
    if not separator:
        name, strength_text = value, ""
    matches = [row for row in catalog_rows if value == row["comfy_name"] or name in {row["comfy_name"], row["filename"], Path(row["filename"]).stem}]
    if len(matches) > 1:
        raise ValueError(f"ambiguous LoRA name: {name}")
    row = matches[0] if matches else None
    comfy_name = row["comfy_name"] if row else name
    strength = float(strength_text) if strength_text else float(row.get("recommended_strength") or 0.8) if row else 0.8
    if not -8.0 <= strength <= 8.0:
        raise ValueError(f"strength outside supported slider range: {strength}")
    return LoraSpec(comfy_name, strength)


def parse_roi(value: str | None) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    parts = tuple(float(part) for part in value.split(","))
    if len(parts) != 4 or parts[2] <= 0 or parts[3] <= 0:
        raise ValueError("ROI must be normalized x,y,width,height")
    return parts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--lora", action="append", default=[], help="ComfyUI LoRA name, optionally @strength; repeatable")
    parser.add_argument("--catalog", type=Path, help="Catalog JSON for exact-name/strength resolution")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runner", type=Path, default=Path(__file__).with_name("h3_i2v_run.py"))
    parser.add_argument("--comfy-input", type=Path, help="Copy reference here and pass its basename to ComfyUI")
    parser.add_argument("--seed", type=int, default=41001)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=384)
    parser.add_argument("--length", type=int, default=73)
    parser.add_argument("--steps", type=int, default=16)
    parser.add_argument("--prompt-prefix", default="", help="Prepended identically to baseline and every candidate")
    parser.add_argument("--face-roi")
    parser.add_argument("--skin-roi")
    parser.add_argument("--no-face", action="store_true")
    parser.add_argument("--render-timeout", type=float, default=900.0)
    parser.add_argument("--queue-timeout", type=float, default=900.0)
    parser.add_argument("--measure-video", type=Path, help="Only measure an existing video; no render")
    args = parser.parse_args(argv)
    if not args.reference:
        parser.error("--reference is required")
    if args.measure_video:
        result = measure_video(args.measure_video, reference=args.reference, face_roi=parse_roi(args.face_roi),
                               skin_roi=parse_roi(args.skin_roi), no_face=args.no_face)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        _write_json(args.output_dir / "measurement.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if not args.prompt_file or not args.audio or not args.lora:
        parser.error("benchmark requires --prompt-file, --audio and at least one --lora")
    catalog_rows = []
    if args.catalog:
        catalog_rows = json.loads(args.catalog.read_text(encoding="utf-8")).get("loras", [])
    loras = [parse_lora(value, catalog_rows) for value in args.lora]
    reference_name = ""
    if args.comfy_input:
        args.comfy_input.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.reference, args.comfy_input / args.reference.name)
        reference_name = args.reference.name
    config = BenchConfig(
        reference=args.reference, prompt_file=args.prompt_file, audio=args.audio,
        output_dir=args.output_dir, loras=loras, runner=args.runner, reference_name=reference_name,
        seed=args.seed, width=args.width, height=args.height, length=args.length, steps=args.steps,
        prompt_prefix=args.prompt_prefix,
        face_roi=parse_roi(args.face_roi), skin_roi=parse_roi(args.skin_roi), no_face=args.no_face,
        comfy_url=os.environ.get("COMFYUI_PROD_URL", "http://127.0.0.1:8188"),
        queue_timeout_s=args.queue_timeout, render_timeout_s=args.render_timeout,
        render_wait_s=min(840, int(args.render_timeout) - 30),
    )
    summary = run_bench(config)
    print(json.dumps({"output_dir": str(args.output_dir), "renders": len(summary["renders"]),
                      "verdicts": {r["label"]: r.get("comparison", {}).get("verdict", r["status"]) for r in summary["renders"]}},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
