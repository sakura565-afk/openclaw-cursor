"""HTTP-клиент ComfyUI и выбор workflow под задачу мебели."""

from __future__ import annotations

import io
import os
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import MachineProfile, resolve_profile
from .workflow_builders import (
    build_checkpoint_inpaint_graph,
    build_flux_fill_gguf_graph,
    build_qwen_masked_inpaint_graph,
    load_ui_workflow_api_path,
    workflow_path_candidates,
)


class ComfyUIError(RuntimeError):
    """Понятная ошибка API ComfyUI."""


@dataclass
class WorkflowChoice:
    """Выбранный граф и способ его получения."""

    kind: str
    prompt: dict[str, Any]
    workflow_file: str | None = None
    notes: str = ""


class ComfyUIClient:
    """Минимальный клиент: /prompt, /history, /view, /upload/image."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_sec: float = 120.0,
        poll_interval: float = 1.0,
        client_id: str | None = None,
        opener: Callable[..., Any] | None = None,
        token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.poll_interval = poll_interval
        self.client_id = client_id or str(uuid.uuid4())
        self._opener = opener or urllib.request.urlopen
        # Токен guard'а перед ComfyUI (нужен, когда ComfyUI выставлен в интернет
        # через tailscale serve: сам ComfyUI аутентификации не имеет).
        self.token = token or os.environ.get("FURNITURE_COMFY_M2_TOKEN", "").strip()

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        raw_body: bytes | None = None,
        content_type: str = "application/json",
        query: dict[str, str] | None = None,
    ) -> bytes:
        url = self.base_url + path
        if query:
            qs = "&".join(f"{k}={urllib.parse.quote(v)}" for k, v in query.items())
            url = f"{url}?{qs}"
        data = raw_body
        headers: dict[str, str] = {}
        if self.token:
            headers["X-Furniture-Token"] = self.token
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener(req, timeout=self.timeout_sec) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace") if exc.fp else ""
            raise ComfyUIError(f"HTTP {exc.code} {method} {path}: {body}") from exc
        except TimeoutError as exc:
            raise ComfyUIError(f"Таймаут {method} {path} ({self.timeout_sec}s)") from exc
        except urllib.error.URLError as exc:
            raise ComfyUIError(f"Сеть {method} {path}: {exc.reason}") from exc

    def submit_workflow(self, prompt_json: dict[str, Any]) -> str:
        """POST /prompt → prompt_id."""
        body = {"prompt": prompt_json, "client_id": self.client_id}
        raw = self._request("POST", "/prompt", body)
        data = json.loads(raw.decode("utf-8"))
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(f"ComfyUI не вернул prompt_id: {data}")
        return str(prompt_id)

    def get_history_entry(self, prompt_id: str) -> dict[str, Any] | None:
        raw = self._request("GET", f"/history/{prompt_id}")
        data = json.loads(raw.decode("utf-8"))
        return data.get(prompt_id)

    def wait_for_result(
        self,
        prompt_id: str,
        timeout_sec: float | None = None,
    ) -> dict[str, Any]:
        """Ожидание завершения в /history/<prompt_id>."""
        limit = timeout_sec if timeout_sec is not None else 900.0
        start = time.monotonic()
        while time.monotonic() - start < limit:
            entry = self.get_history_entry(prompt_id)
            if entry is not None:
                status = entry.get("status", {})
                if status.get("completed") or entry.get("outputs"):
                    return entry
            time.sleep(self.poll_interval)
        raise ComfyUIError(
            f"Истекло ожидание prompt_id={prompt_id} ({limit:.0f}s). "
            "Проверьте очередь ComfyUI и логи нод."
        )

    def download_view(self, image_ref: dict[str, str]) -> bytes:
        """GET /view для output-изображения."""
        params = {
            "filename": image_ref["filename"],
            "subfolder": image_ref.get("subfolder", ""),
            "type": image_ref.get("type", "output"),
        }
        return self._request("GET", "/view", query=params)

    def upload_image(self, image_bytes: bytes, filename: str) -> dict[str, Any]:
        """Загрузка в input через multipart (stdlib)."""
        boundary = f"----furniture{uuid.uuid4().hex}"
        body = io.BytesIO()
        for name, value in (("type", "input"), ("overwrite", "true")):
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.write(value.encode() + b"\r\n")
        body.write(f"--{boundary}\r\n".encode())
        body.write(
            f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode()
        )
        body.write(b"Content-Type: image/png\r\n\r\n")
        body.write(image_bytes)
        body.write(b"\r\n")
        body.write(f"--{boundary}--\r\n".encode())
        raw = body.getvalue()
        upload_headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        if self.token:
            upload_headers["X-Furniture-Token"] = self.token
        req = urllib.request.Request(
            f"{self.base_url}/upload/image",
            data=raw,
            headers=upload_headers,
            method="POST",
        )
        try:
            with self._opener(req, timeout=self.timeout_sec) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            # Запасной путь: некоторые сборки ComfyUI отдают 500 на /upload/image
            # (asset_manager/БД), но LoadImage умеет брать файлы прямо из input/.
            fallback = self._upload_via_input_dir(image_bytes, filename)
            if fallback is not None:
                return fallback
            raise ComfyUIError(f"upload/image failed: {exc}") from exc

    def _upload_via_input_dir(self, image_bytes: bytes, filename: str) -> dict[str, Any] | None:
        """Положить файл прямо в input/ ComfyUI (обход сломанного /upload/image)."""
        input_dir = os.environ.get("FURNITURE_COMFY_INPUT_DIR", "").strip()
        if not input_dir:
            return None
        try:
            os.makedirs(input_dir, exist_ok=True)
            target = os.path.join(input_dir, filename)
            with open(target, "wb") as fh:
                fh.write(image_bytes)
        except OSError:
            return None
        return {"name": filename, "subfolder": "", "type": "input"}

    @staticmethod
    def first_output_image(history_entry: dict[str, Any]) -> dict[str, str] | None:
        outputs = history_entry.get("outputs") or {}
        for node_out in outputs.values():
            for img in node_out.get("images") or []:
                if img.get("filename"):
                    return img
        return None


def _norm_task(task: dict[str, Any]) -> tuple[str, str, str]:
    furniture = str(task.get("furniture_type", task.get("furniture", "sofa"))).lower()
    operation = str(
        task.get("operation", task.get("task", task.get("goal", "reupholstery")))
    ).lower()
    workflow_hint = str(task.get("workflow", task.get("engine", "auto"))).lower()
    return furniture, operation, workflow_hint


def pick_workflow(
    task: dict[str, Any],
    profile: MachineProfile,
    *,
    src_name: str = "furniture_src.png",
    mask_name: str = "furniture_mask.png",
    positive: str | None = None,
    seed: int | None = None,
) -> WorkflowChoice:
    """Подбор inpaint-графа по профилю задачи (диван/стул, цвет/фурнитура).

    Приоритет:
    - flux_fill для крупной смены фактуры / hardware на M1/M2 с GGUF
    - checkpoint inpaint для M2 Juggernaut Inpaint
    - qwen masked edit по умолчанию (как в qwen_masked_fix)
    JSON klein/face из comfyui_workflows/ — только при явном workflow (не мебель).
    """
    furniture, operation, workflow_hint = _norm_task(task)
    prompt = positive or str(
        task.get(
            "prompt",
            "high quality furniture product photo, same silhouette and legs, "
            "realistic fabric upholstery, accurate seams, studio lighting",
        )
    )
    seed_val = int(seed if seed is not None else task.get("seed", 0))
    denoise = float(task.get("denoise", 0.52))
    steps = int(task.get("steps", 24))

    static = workflow_path_candidates()
    if workflow_hint in static and workflow_hint.startswith("face"):
        path = static[workflow_hint]
        return WorkflowChoice(
            kind=workflow_hint,
            prompt=load_ui_workflow_api_path(path),
            workflow_file=str(path),
            notes="Шаблон из comfyui_workflows; для лица, не для обивки.",
        )

    use_flux = workflow_hint in ("flux", "flux_fill", "fill") or operation in (
        "texture",
        "hardware",
        "фурнитура",
    )
    use_sdxl = workflow_hint in ("sdxl", "juggernaut", "checkpoint") or (
        profile.name in ("m2", "remote-peer") and operation in ("color", "цвет")
    )
    if workflow_hint == "sdxl" and task.get("sdxl_ckpt"):
        # явный чекпоинт из задачи перекрывает профиль
        use_flux = False

    if use_flux and profile.models.flux_fill_gguf:
        graph = build_flux_fill_gguf_graph(
            src_name=src_name,
            mask_name=mask_name,
            positive=prompt,
            profile=profile,
            seed=seed_val,
            denoise=float(task.get("flux_denoise", 1.0)),
            steps=int(task.get("flux_steps", 20)),
            cfg=float(task.get("flux_cfg", 3.5)),
            filename_prefix=f"furniture_{furniture}_{operation}",
        )
        return WorkflowChoice(
            kind="flux_fill_gguf",
            prompt=graph,
            notes=f"Flux Fill ({profile.models.flux_fill_gguf}); нужны GGUF-ноды на хосте.",
        )

    if use_sdxl and profile.models.checkpoint:
        graph = build_checkpoint_inpaint_graph(
            src_name=src_name,
            mask_name=mask_name,
            positive=prompt,
            profile=profile,
            seed=seed_val,
            denoise=float(task.get("sdxl_denoise", 0.85)),
            steps=int(task.get("sdxl_steps", 32)),
            cfg=float(task.get("sdxl_cfg", 7.0)),
            sampler_name=str(task.get("sdxl_sampler", "dpmpp_2m")),
            scheduler=str(task.get("sdxl_scheduler", "karras")),
            grow_mask_by=int(task.get("sdxl_grow_mask_by", 16)),
            ckpt_override=task.get("sdxl_ckpt"),
            negative=task.get("negative"),
            filename_prefix=f"furniture_{furniture}_{operation}",
        )
        return WorkflowChoice(
            kind="sdxl_inpaint",
            prompt=graph,
            notes=f"Checkpoint inpaint: {profile.models.checkpoint}",
        )

    graph = build_qwen_masked_inpaint_graph(
        src_name=src_name,
        mask_name=mask_name,
        positive=prompt,
        profile=profile,
        seed=seed_val,
        denoise=denoise,
        steps=steps,
        filename_prefix=f"furniture_{furniture}_{operation}",
    )
    return WorkflowChoice(
        kind="qwen_masked_inpaint",
        prompt=graph,
        notes="Qwen masked edit — основной локальный путь для смены обивки/цвета.",
    )


def client_for_task(task: dict[str, Any]) -> ComfyUIClient:
    host = task.get("comfy_host", task.get("host", "local"))
    profile = resolve_profile(str(host) if host else None)
    return ComfyUIClient(profile.comfy_url)