"""Сквозной конвейер мебели: план (M2) → inpaint ComfyUI (M1) → QC (M2)."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .peers import resolve_peer
from .runner import RunResult, run_task

COMFYUI_WORK = os.environ.get("COMFYUI_PROD", "http://127.0.0.1:8188").rstrip("/")
_FURNITURE_DB = Path(__file__).resolve().parents[2] / "furniture_sales_database"


@dataclass(slots=True)
class FurnitureStageResult:
    stage: str
    peer: str
    ok: bool
    detail: dict[str, Any]


def _comfy_post_prompt(workflow: dict[str, Any], *, timeout: float = 240) -> str:
    """POST /prompt на локальный ComfyUI M1; вернуть prompt_id."""
    url = f"{COMFYUI_WORK}/prompt"
    body = json.dumps({"prompt": workflow, "client_id": "cluster-furniture"}).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    payload = json.loads(raw) if raw.strip() else {}
    pid = payload.get("prompt_id")
    if not pid:
        raise RuntimeError(f"ComfyUI не вернул prompt_id: {payload!r}")
    return str(pid)


def _comfy_wait_history(
    prompt_id: str,
    *,
    timeout: float = 2400,
    poll: float = 8,
) -> dict[str, Any]:
    """Ожидание завершения по GET /history/{prompt_id}."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        url = f"{COMFYUI_WORK}/history/{prompt_id}"
        req = Request(url, method="GET")
        try:
            with urlopen(req, timeout=45) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            hist = json.loads(raw) if raw.strip() else {}
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(poll)
            continue
        entry = hist.get(prompt_id) if isinstance(hist, dict) else None
        if not entry:
            time.sleep(poll)
            continue
        last = entry
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            msgs = status.get("messages", [])
            raise RuntimeError(f"ComfyUI error: {json.dumps(msgs, ensure_ascii=False)[:800]}")
        outputs = entry.get("outputs") or {}
        if outputs:
            return {"prompt_id": prompt_id, "outputs": outputs, "status": status}
        time.sleep(poll)
    raise TimeoutError(
        f"ComfyUI history timeout {timeout}s; последний снимок: {json.dumps(last)[:400]}"
    )


def stage1_plan(task: dict[str, Any], *, timeout: float = 600) -> FurnitureStageResult:
    """M2: разбор задачи, выбор ткани, ТЗ и промпты через Hermes home."""
    peer = resolve_peer("home")
    brief = task.get("description") or task.get("task") or json.dumps(task, ensure_ascii=False)
    fabric = task.get("fabric", "не указана")
    prompt = (
        "Этап furniture stage1_plan (навык furniture-processing). "
        f"Задача: {brief}\nТкань: {fabric}\n"
        "Верни JSON с полями: fabric_choice, technical_spec, positive_prompt, negative_prompt, "
        "inpaint_mask_hint, qc_criteria."
    )
    result = run_task(peer, prompt, wait=True, timeout=timeout)
    ok = result.status in {"completed", "done", "finished", "succeeded"} and not result.error
    return FurnitureStageResult(
        stage="stage1_plan",
        peer=peer.name,
        ok=ok,
        detail={"run": asdict(result), "parsed_output": result.output},
    )


def stage2_render(
    task: dict[str, Any],
    plan_output: str | None = None,
    *,
    timeout: float = 2400,
) -> FurnitureStageResult:
    """M1: ComfyUI inpaint — workflow из task или минимальный flux-fill шаблон."""
    workflow = task.get("comfy_workflow")
    if workflow is None:
        positive = task.get("positive_prompt") or (
            plan_output or "photorealistic furniture product photo, studio lighting"
        )
        if isinstance(positive, str) and positive.strip().startswith("{"):
            try:
                parsed = json.loads(positive)
                positive = parsed.get("positive_prompt") or positive
            except json.JSONDecodeError:
                pass
        workflow = task.get("workflow") or _default_inpaint_workflow(
            str(positive)[:2000],
            str(task.get("negative_prompt", "blurry, deformed, watermark")),
            task.get("input_image", "input.png"),
        )

    try:
        prompt_id = _comfy_post_prompt(workflow)
        hist = _comfy_wait_history(prompt_id, timeout=timeout)
        detail = {"comfyui_base": COMFYUI_WORK, "history": hist}
        return FurnitureStageResult(
            stage="stage2_render",
            peer="work",
            ok=True,
            detail=detail,
        )
    except Exception as exc:  # noqa: BLE001
        return FurnitureStageResult(
            stage="stage2_render",
            peer="work",
            ok=False,
            detail={"error": str(exc), "comfyui_base": COMFYUI_WORK},
        )


def _default_inpaint_workflow(positive: str, negative: str, image_name: str) -> dict[str, Any]:
    """Минимальный граф inpaint; реальные ноды задаются в task.json (comfy_workflow)."""
    return {
        "10": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "11": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": positive, "clip": ["clip", 0]},
        },
        "12": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["clip", 0]},
        },
        "20": {
            "class_type": "SaveImage",
            "inputs": {"images": ["10", 0], "filename_prefix": "furniture_cluster"},
        },
    }


def stage3_qc(
    task: dict[str, Any],
    render_detail: dict[str, Any],
    *,
    timeout: float = 600,
) -> FurnitureStageResult:
    """M2: QC агентом и запись в furniture_sales_database (SQLite, если есть)."""
    peer = resolve_peer("home")
    qc_prompt = (
        "Этап furniture stage3_qc. Проверь результат рендера мебели по критериям продаж. "
        f"Задача: {json.dumps(task, ensure_ascii=False)[:1500]}\n"
        f"Рендер (сводка): {json.dumps(render_detail, ensure_ascii=False)[:2000]}\n"
        "Верни verdict: pass|fail, замечания, поля для CRM."
    )
    result = run_task(peer, qc_prompt, wait=True, timeout=timeout)
    record_path = _FURNITURE_DB / "cluster_qc_log.jsonl"
    record = {
        "task_id": task.get("id", "unknown"),
        "verdict_run": asdict(result),
        "render_prompt_id": render_detail.get("history", {}).get("prompt_id"),
    }
    try:
        record_path.parent.mkdir(parents=True, exist_ok=True)
        with record_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        record["write_error"] = str(exc)

    ok = result.status in {"completed", "done", "finished", "succeeded"}
    return FurnitureStageResult(
        stage="stage3_qc",
        peer=peer.name,
        ok=ok and not result.error,
        detail={"run": asdict(result), "qc_log": str(record_path)},
    )


def run_pipeline(task_file: Path, *, stop_on_error: bool = True) -> dict[str, Any]:
    """Полный конвейер из JSON-файла задачи."""
    raw = task_file.read_text(encoding="utf-8")
    task = json.loads(raw)
    report: dict[str, Any] = {"task_file": str(task_file), "stages": []}

    s1 = stage1_plan(task)
    report["stages"].append(asdict(s1))
    if stop_on_error and not s1.ok:
        report["status"] = "failed"
        return report

    plan_out = s1.detail.get("parsed_output")
    s2 = stage2_render(task, plan_out)
    report["stages"].append(asdict(s2))
    if stop_on_error and not s2.ok:
        report["status"] = "failed"
        return report

    s3 = stage3_qc(task, s2.detail)
    report["stages"].append(asdict(s3))
    report["status"] = "ok" if s3.ok else "failed"
    return report