#!/usr/bin/env python3
"""
Универсальный пайплайн генерации видео мебели через ComfyUI API:
1) Генерация базовых кадров из фото товара (rotate/zoom/pan + изменение света)
2) Интерполяция кадров через RIFE (например 16 -> 60 FPS)
3) Финальный upscale через SUPIR (если ноды доступны)

Скрипт ориентирован на ComfyUI portable (в том числе Windows portable),
но работает с любым сервером ComfyUI, доступным по HTTP (по умолчанию localhost:8188).

Зависимости (pip):
    pip install requests pillow

Рекомендуемые внешние утилиты:
    ffmpeg (для сборки финального mp4)
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageEnhance


DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
DEFAULT_DURATION = 5.0
DEFAULT_TARGET_FPS = 60
DEFAULT_BASE_GEN_FPS = 16
DEFAULT_CFG = 6.0
DEFAULT_STEPS = 28
DEFAULT_DENOISE = 0.35
DEFAULT_RIFE_MODELS_DIR = (
    r"C:\Users\user\comfyui\ComfyUI_windows_portable\ComfyUI\models\frame_interpolation"
)
DEFAULT_CHECKPOINT = "realisticVisionV60B1_v12.safetensors"
DEFAULT_LOG_PATH = Path("memory/comfy_video_log.md")

NEGATIVE_PROMPT = (
    "low quality, blur, distorted geometry, text, watermark, "
    "logo, artifacts, noisy, deformed furniture"
)


class PipelineError(RuntimeError):
    """Бизнес-ошибка пайплайна."""


@dataclass
class PipelineConfig:
    input_image: Path
    effect: str
    duration: float = DEFAULT_DURATION
    fps: int = DEFAULT_TARGET_FPS
    output_video: Path = Path("output_video.mp4")
    frames_dir: Path = Path("frames")
    comfy_url: str = DEFAULT_COMFY_URL
    checkpoint: str = DEFAULT_CHECKPOINT
    rife_models_dir: str = DEFAULT_RIFE_MODELS_DIR
    rife_model_name: Optional[str] = None
    use_supir: bool = True
    supir_scale: float = 1.5
    width: int = 768
    height: int = 768
    base_gen_fps: int = DEFAULT_BASE_GEN_FPS
    cfg: float = DEFAULT_CFG
    steps: int = DEFAULT_STEPS
    denoise: float = DEFAULT_DENOISE
    seed: int = 424242
    positive_prompt: str = (
        "high quality studio furniture product photo, realistic materials, "
        "soft global illumination, physically accurate lighting, 8k details"
    )
    negative_prompt: str = NEGATIVE_PROMPT
    retries: int = 4
    retry_backoff_sec: float = 1.5


class MarkdownLogger:
    """Простой markdown-логгер в memory/comfy_video_log.md."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, title: str, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with self.path.open("a", encoding="utf-8") as f:
            f.write(f"## {ts} | {title}\n\n{message}\n\n")


class ComfyClient:
    """Клиент для ComfyUI HTTP API с retry-логикой."""

    def __init__(self, base_url: str, retries: int, backoff: float, logger: MarkdownLogger):
        self.base_url = base_url.rstrip("/")
        self.client_id = str(uuid.uuid4())
        self.retries = retries
        self.backoff = backoff
        self.logger = logger
        self.session = requests.Session()

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        last_exc: Optional[Exception] = None
        url = f"{self.base_url}{path}"
        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.request(method, url, timeout=120, **kwargs)
                resp.raise_for_status()
                return resp
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                sleep_sec = self.backoff * attempt
                self.logger.log(
                    "HTTP retry",
                    f"Запрос {method} {url} не удался (attempt={attempt}/{self.retries}): {exc}",
                )
                time.sleep(sleep_sec)
        raise PipelineError(f"Не удалось выполнить запрос {method} {url}: {last_exc}") from last_exc

    def ping(self) -> None:
        self._request("GET", "/system_stats")

    def get_object_info(self) -> Dict[str, Any]:
        return self._request("GET", "/object_info").json()

    def upload_image(self, image_path: Path, subfolder: str = "") -> Dict[str, Any]:
        with image_path.open("rb") as f:
            files = {"image": (image_path.name, f, "image/png")}
            data = {"subfolder": subfolder, "type": "input"}
            return self._request("POST", "/upload/image", files=files, data=data).json()

    def queue_prompt(self, prompt: Dict[str, Any]) -> str:
        payload = {"prompt": prompt, "client_id": self.client_id}
        data = self._request("POST", "/prompt", json=payload).json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise PipelineError(f"ComfyUI не вернул prompt_id. Ответ: {data}")
        return prompt_id

    def wait_prompt(self, prompt_id: str, timeout_sec: int = 600) -> Dict[str, Any]:
        started = time.time()
        while time.time() - started < timeout_sec:
            history = self._request("GET", f"/history/{prompt_id}").json()
            if prompt_id in history:
                return history[prompt_id]
            time.sleep(1.0)
        raise PipelineError(f"Таймаут ожидания prompt_id={prompt_id}")

    def download_view_image(self, filename: str, subfolder: str, folder_type: str, out_path: Path) -> None:
        params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
        resp = self._request("GET", "/view", params=params)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(resp.content)


def retry(func):
    """Декоратор retry для локальных операций (не HTTP)."""

    def wrapper(*args, **kwargs):
        self = args[0]
        last_exc = None
        for attempt in range(1, self.cfg.retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                sleep_sec = self.cfg.retry_backoff_sec * attempt
                self.logger.log(
                    "Local retry",
                    f"{func.__name__} attempt={attempt}/{self.cfg.retries} failed: {exc}",
                )
                time.sleep(sleep_sec)
        raise PipelineError(f"{func.__name__} failed after retries: {last_exc}") from last_exc

    return wrapper


class ComfyVideoPipeline:
    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        self.logger = MarkdownLogger(DEFAULT_LOG_PATH)
        self.client = ComfyClient(
            base_url=cfg.comfy_url,
            retries=cfg.retries,
            backoff=cfg.retry_backoff_sec,
            logger=self.logger,
        )
        self.frames_raw_dir = cfg.frames_dir / "raw"
        self.frames_generated_dir = cfg.frames_dir / "generated"
        self.frames_interpolated_dir = cfg.frames_dir / "interpolated"
        self.frames_upscaled_dir = cfg.frames_dir / "upscaled"

    def run(self) -> None:
        self.logger.log(
            "Pipeline start",
            f"input={self.cfg.input_image}, effect={self.cfg.effect}, duration={self.cfg.duration}, "
            f"target_fps={self.cfg.fps}, base_gen_fps={self.cfg.base_gen_fps}",
        )
        self._prepare_dirs()
        self.client.ping()
        object_info = self.client.get_object_info()

        raw_frames = self._create_effect_frames()
        generated_frames = self._generate_sd_frames(raw_frames)
        interpolated_frames = self._interpolate_frames_rife(generated_frames, object_info)

        final_frames = interpolated_frames
        if self.cfg.use_supir:
            final_frames = self._upscale_supir(interpolated_frames, object_info)

        self._encode_video(final_frames, self.cfg.output_video, fps=self.cfg.fps)
        self.logger.log("Pipeline done", f"Финальный файл: {self.cfg.output_video.resolve()}")

    def _prepare_dirs(self) -> None:
        for p in [
            self.cfg.frames_dir,
            self.frames_raw_dir,
            self.frames_generated_dir,
            self.frames_interpolated_dir,
            self.frames_upscaled_dir,
        ]:
            p.mkdir(parents=True, exist_ok=True)

    @retry
    def _create_effect_frames(self) -> List[Path]:
        img = Image.open(self.cfg.input_image).convert("RGB")
        img = img.resize((self.cfg.width, self.cfg.height), Image.Resampling.LANCZOS)
        total_frames = max(2, int(self.cfg.duration * self.cfg.base_gen_fps))
        saved: List[Path] = []

        for i in range(total_frames):
            t = i / max(1, total_frames - 1)
            frame = self._apply_effect(img, t)

            # Добавляем управляемое изменение освещения (light change)
            brightness = 0.94 + 0.14 * math.sin(t * math.pi * 2.0)
            frame = ImageEnhance.Brightness(frame).enhance(brightness)

            out = self.frames_raw_dir / f"raw_{i:04d}.png"
            frame.save(out, format="PNG")
            saved.append(out)

        self.logger.log("Frames raw", f"Создано сырьевых кадров: {len(saved)}")
        return saved

    def _apply_effect(self, img: Image.Image, t: float) -> Image.Image:
        w, h = img.size
        effect = self.cfg.effect

        if effect == "rotate":
            angle = -6 + 12 * t
            transformed = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False)
            return transformed

        if effect == "zoom":
            scale = 1.0 + 0.16 * t  # плавный зум-in
            nw, nh = int(w / scale), int(h / scale)
            left = (w - nw) // 2
            top = (h - nh) // 2
            crop = img.crop((left, top, left + nw, top + nh))
            return crop.resize((w, h), Image.Resampling.LANCZOS)

        if effect == "pan":
            # Горизонтальный pan слева направо
            shift = int((w * 0.12) * (t - 0.5) * 2.0)
            left = max(0, min(w // 6, w // 2 + shift - w // 2))
            right = min(w, left + w - w // 6)
            crop = img.crop((left, 0, right, h))
            return crop.resize((w, h), Image.Resampling.LANCZOS)

        raise PipelineError(f"Неподдерживаемый effect={effect}")

    @retry
    def _generate_sd_frames(self, raw_frames: List[Path]) -> List[Path]:
        generated: List[Path] = []
        for i, frame in enumerate(raw_frames):
            upload_info = self.client.upload_image(frame)
            comfy_input_name = upload_info.get("name") or frame.name
            comfy_input_subfolder = upload_info.get("subfolder", "")

            prompt_text = self._build_dynamic_prompt(i, len(raw_frames))
            workflow = self._workflow_img2img(
                image_name=comfy_input_name,
                image_subfolder=comfy_input_subfolder,
                prompt_text=prompt_text,
                seed=self.cfg.seed + i,
            )

            prompt_id = self.client.queue_prompt(workflow)
            result = self.client.wait_prompt(prompt_id, timeout_sec=900)
            image_ref = self._extract_first_image_ref(result)
            if not image_ref:
                raise PipelineError(f"Не найден output image для кадра {i}. history={result}")

            out = self.frames_generated_dir / f"gen_{i:04d}.png"
            self.client.download_view_image(
                filename=image_ref["filename"],
                subfolder=image_ref["subfolder"],
                folder_type=image_ref["type"],
                out_path=out,
            )
            generated.append(out)

        self.logger.log("Frames generated", f"Сгенерировано SD кадров: {len(generated)}")
        return generated

    def _build_dynamic_prompt(self, i: int, total: int) -> str:
        t = i / max(1, total - 1)
        if t < 0.33:
            light = "warm directional light, soft highlights"
        elif t < 0.66:
            light = "neutral daylight, balanced shadows"
        else:
            light = "slightly dramatic side light, cinematic contrast"
        return f"{self.cfg.positive_prompt}, {light}"

    def _workflow_img2img(
        self,
        image_name: str,
        image_subfolder: str,
        prompt_text: str,
        seed: int,
    ) -> Dict[str, Any]:
        # Базовый workflow на стандартных нодах ComfyUI
        return {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": self.cfg.checkpoint}},
            "2": {"class_type": "LoadImage", "inputs": {"image": image_name, "upload": "image"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt_text, "clip": ["1", 1]}},
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": self.cfg.negative_prompt, "clip": ["1", 1]},
            },
            "5": {"class_type": "VAEEncode", "inputs": {"pixels": ["2", 0], "vae": ["1", 2]}},
            "6": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": self.cfg.steps,
                    "cfg": self.cfg.cfg,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": self.cfg.denoise,
                    "model": ["1", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0],
                },
            },
            "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
            "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "comfy_video_gen", "images": ["7", 0]}},
        }

    @retry
    def _interpolate_frames_rife(
        self, generated_frames: List[Path], object_info: Dict[str, Any]
    ) -> List[Path]:
        if not generated_frames:
            raise PipelineError("Нет кадров для интерполяции.")

        model_name = self._select_rife_model_name()
        self.logger.log("RIFE model", f"Выбрана RIFE модель: {model_name}")

        # Объединяем сгенерированные кадры в исходный видео-файл (base_gen_fps)
        pre_interp_video = self.cfg.frames_dir / "pre_interp.mp4"
        self._encode_video(generated_frames, pre_interp_video, fps=self.cfg.base_gen_fps)

        # Пытаемся построить workflow для интерполяции через доступные ноды.
        workflow = self._build_rife_workflow_dynamic(
            object_info=object_info,
            input_video=pre_interp_video.name,
            model_name=model_name,
            target_fps=self.cfg.fps,
        )
        if workflow is None:
            raise PipelineError(
                "Не удалось найти подходящие RIFE-ноды в ComfyUI object_info. "
                "Убедитесь, что установлен пакет frame interpolation и ноды активны."
            )

        # Загружаем видео в input ComfyUI через upload/image не подходит.
        # Поэтому копируем в локальный input-dir, если он доступен по стандартной структуре.
        # Для portable обычно ComfyUI/input.
        input_dir = Path.cwd() / "ComfyUI" / "input"
        if input_dir.exists():
            input_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(pre_interp_video, input_dir / pre_interp_video.name)
        else:
            self.logger.log(
                "RIFE input warning",
                "Папка ComfyUI/input не найдена рядом со скриптом. "
                "Убедитесь, что pre_interp.mp4 доступен ComfyUI в input директории.",
            )

        prompt_id = self.client.queue_prompt(workflow)
        result = self.client.wait_prompt(prompt_id, timeout_sec=1800)
        video_ref = self._extract_first_video_ref(result)
        if not video_ref:
            raise PipelineError(f"ComfyUI не вернул видео после RIFE. history={result}")

        interpolated_video = self.cfg.frames_dir / "interpolated.mp4"
        self.client.download_view_image(
            filename=video_ref["filename"],
            subfolder=video_ref["subfolder"],
            folder_type=video_ref["type"],
            out_path=interpolated_video,
        )

        self._extract_video_to_frames(interpolated_video, self.frames_interpolated_dir, "interp")
        frames = sorted(self.frames_interpolated_dir.glob("interp_*.png"))
        self.logger.log("RIFE done", f"Интерполировано кадров: {len(frames)}")
        return frames

    def _build_rife_workflow_dynamic(
        self, object_info: Dict[str, Any], input_video: str, model_name: str, target_fps: int
    ) -> Optional[Dict[str, Any]]:
        # Популярные class_type для разных пакетов интерполяции/видео в ComfyUI.
        classes = set(object_info.keys())
        load_video_candidates = ["VHS_LoadVideo", "LoadVideo"]
        load_rife_candidates = ["RIFE VFI", "RIFE_VFI", "VFI_RIFE", "RIFEModelLoader", "VFI_LoadModel"]
        interp_candidates = ["VFI_Interpolation", "RIFE Interpolate", "RIFE_Interpolation"]
        save_video_candidates = ["VHS_VideoCombine", "SaveVideo"]

        lv = next((c for c in load_video_candidates if c in classes), None)
        lr = next((c for c in load_rife_candidates if c in classes), None)
        it = next((c for c in interp_candidates if c in classes), None)
        sv = next((c for c in save_video_candidates if c in classes), None)
        if not all([lv, lr, it, sv]):
            self.logger.log(
                "RIFE node detection",
                f"Ноды не найдены полностью: load_video={lv}, load_rife={lr}, interp={it}, save_video={sv}",
            )
            return None

        # Универсальная схема, может потребовать корректировки конкретных входов под вашу сборку.
        workflow = {
            "1": {"class_type": lv, "inputs": {"video": input_video}},
            "2": {"class_type": lr, "inputs": {"model_name": model_name}},
            "3": {
                "class_type": it,
                "inputs": {
                    "frames": ["1", 0],
                    "model": ["2", 0],
                    "target_fps": target_fps,
                },
            },
            "4": {
                "class_type": sv,
                "inputs": {"images": ["3", 0], "filename_prefix": "comfy_video_interp"},
            },
        }
        return workflow

    def _select_rife_model_name(self) -> str:
        # Ищем модель в явно заданной директории из требования.
        models_dir = Path(self.cfg.rife_models_dir)
        if not models_dir.exists():
            self.logger.log(
                "RIFE path warning",
                f"Путь моделей RIFE не существует: {models_dir}. Будет использовано имя по умолчанию rife47.",
            )
            return self.cfg.rife_model_name or "rife47"

        model_files = sorted([p for p in models_dir.iterdir() if p.is_file() or p.is_dir()])
        if not model_files:
            return self.cfg.rife_model_name or "rife47"
        if self.cfg.rife_model_name:
            return self.cfg.rife_model_name
        return model_files[0].name

    @retry
    def _upscale_supir(self, interpolated_frames: List[Path], object_info: Dict[str, Any]) -> List[Path]:
        classes = set(object_info.keys())
        if "SUPIR_Upscale" not in classes and "SUPIR" not in classes:
            self.logger.log(
                "SUPIR skipped",
                "SUPIR ноды не найдены в текущей ComfyUI сборке. Пропускаю upscale.",
            )
            return interpolated_frames

        upscaled: List[Path] = []
        supir_node = "SUPIR_Upscale" if "SUPIR_Upscale" in classes else "SUPIR"
        for i, frame in enumerate(interpolated_frames):
            upload_info = self.client.upload_image(frame)
            image_name = upload_info.get("name") or frame.name

            workflow = {
                "1": {"class_type": "LoadImage", "inputs": {"image": image_name, "upload": "image"}},
                "2": {"class_type": supir_node, "inputs": {"image": ["1", 0], "scale": self.cfg.supir_scale}},
                "3": {
                    "class_type": "SaveImage",
                    "inputs": {"images": ["2", 0], "filename_prefix": "comfy_video_supir"},
                },
            }
            prompt_id = self.client.queue_prompt(workflow)
            result = self.client.wait_prompt(prompt_id, timeout_sec=1200)
            image_ref = self._extract_first_image_ref(result)
            if not image_ref:
                raise PipelineError(f"SUPIR output image не найден на кадре {i}.")

            out = self.frames_upscaled_dir / f"up_{i:05d}.png"
            self.client.download_view_image(
                filename=image_ref["filename"],
                subfolder=image_ref["subfolder"],
                folder_type=image_ref["type"],
                out_path=out,
            )
            upscaled.append(out)

        self.logger.log("SUPIR done", f"Upscaled кадров: {len(upscaled)}")
        return upscaled

    def _encode_video(self, frame_paths: List[Path] | Path, output_video: Path, fps: int) -> None:
        output_video.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(frame_paths, Path):
            # Уже готовое видео - просто копируем/перезаписываем при необходимости.
            shutil.copy2(frame_paths, output_video)
            return

        if not frame_paths:
            raise PipelineError("Невозможно собрать видео: список кадров пуст.")

        # ffmpeg через concat демультиплексор по шаблону.
        temp_pattern_dir = output_video.parent / f".tmp_{uuid.uuid4().hex}"
        temp_pattern_dir.mkdir(parents=True, exist_ok=True)
        for i, src in enumerate(frame_paths):
            shutil.copy2(src, temp_pattern_dir / f"frame_{i:05d}.png")

        cmd = [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(temp_pattern_dir / "frame_%05d.png"),
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(output_video),
        ]
        self._run_subprocess(cmd, "ffmpeg encode")
        shutil.rmtree(temp_pattern_dir, ignore_errors=True)

    def _extract_video_to_frames(self, video_path: Path, out_dir: Path, prefix: str) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-y", "-i", str(video_path), str(out_dir / f"{prefix}_%05d.png")]
        self._run_subprocess(cmd, "ffmpeg extract frames")

    def _run_subprocess(self, cmd: List[str], title: str) -> None:
        try:
            proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
            self.logger.log(title, f"Команда выполнена: {' '.join(cmd)}\n\n{proc.stdout[-2000:]}")
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "")[-4000:]
            self.logger.log(title, f"Ошибка команды: {' '.join(cmd)}\n\n{stderr}")
            raise PipelineError(f"Ошибка subprocess ({title}): {stderr}") from exc

    def _extract_first_image_ref(self, history_item: Dict[str, Any]) -> Optional[Dict[str, str]]:
        outputs = history_item.get("outputs", {})
        for node_data in outputs.values():
            images = node_data.get("images", [])
            if images:
                return images[0]
        return None

    def _extract_first_video_ref(self, history_item: Dict[str, Any]) -> Optional[Dict[str, str]]:
        outputs = history_item.get("outputs", {})
        for node_data in outputs.values():
            videos = node_data.get("gifs", []) or node_data.get("videos", [])
            if videos:
                return videos[0]
        return None


def parse_args() -> PipelineConfig:
    parser = argparse.ArgumentParser(
        description="Генерация и интерполяция видео мебели через ComfyUI (SD + RIFE + SUPIR)."
    )
    parser.add_argument("--input_image", required=True, help="Путь к входной фотографии мебели.")
    parser.add_argument(
        "--effect",
        required=True,
        choices=["rotate", "zoom", "pan"],
        help="Тип эффекта камеры: rotate | zoom | pan",
    )
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION, help="Длительность видео в секундах.")
    parser.add_argument("--fps", type=int, default=DEFAULT_TARGET_FPS, help="Целевой FPS итогового видео.")
    parser.add_argument(
        "--output_video",
        default="output_video.mp4",
        help="Путь к финальному mp4 файлу.",
    )
    parser.add_argument("--frames_dir", default="frames", help="Папка для промежуточных кадров.")
    parser.add_argument("--comfy_url", default=DEFAULT_COMFY_URL, help="URL ComfyUI API.")
    parser.add_argument(
        "--checkpoint",
        default=DEFAULT_CHECKPOINT,
        help="Base checkpoint для SD (по требованию: realisticVisionV60B1_v12.safetensors).",
    )
    parser.add_argument(
        "--rife_models_dir",
        default=DEFAULT_RIFE_MODELS_DIR,
        help="Путь к директории моделей RIFE.",
    )
    parser.add_argument("--rife_model_name", default=None, help="Явное имя RIFE модели (опционально).")
    parser.add_argument("--base_gen_fps", type=int, default=DEFAULT_BASE_GEN_FPS, help="FPS базовой SD генерации.")
    parser.add_argument("--width", type=int, default=768, help="Ширина кадра.")
    parser.add_argument("--height", type=int, default=768, help="Высота кадра.")
    parser.add_argument("--steps", type=int, default=DEFAULT_STEPS, help="Количество SD шагов KSampler.")
    parser.add_argument("--cfg", type=float, default=DEFAULT_CFG, help="CFG scale для SD.")
    parser.add_argument("--denoise", type=float, default=DEFAULT_DENOISE, help="Сила denoise в img2img.")
    parser.add_argument("--seed", type=int, default=424242, help="Базовый seed.")
    parser.add_argument("--supir_scale", type=float, default=1.5, help="Коэффициент SUPIR upscale.")
    parser.add_argument(
        "--no_supir",
        action="store_true",
        help="Отключить SUPIR upscale даже если ноды доступны.",
    )
    parser.add_argument("--retries", type=int, default=4, help="Количество retry для API и операций.")
    parser.add_argument("--retry_backoff_sec", type=float, default=1.5, help="Базовая пауза между retry.")

    args = parser.parse_args()
    return PipelineConfig(
        input_image=Path(args.input_image),
        effect=args.effect,
        duration=args.duration,
        fps=args.fps,
        output_video=Path(args.output_video),
        frames_dir=Path(args.frames_dir),
        comfy_url=args.comfy_url,
        checkpoint=args.checkpoint,
        rife_models_dir=args.rife_models_dir,
        rife_model_name=args.rife_model_name,
        use_supir=not args.no_supir,
        supir_scale=args.supir_scale,
        width=args.width,
        height=args.height,
        base_gen_fps=args.base_gen_fps,
        cfg=args.cfg,
        steps=args.steps,
        denoise=args.denoise,
        seed=args.seed,
        retries=args.retries,
        retry_backoff_sec=args.retry_backoff_sec,
    )


def main() -> int:
    cfg = parse_args()
    logger = MarkdownLogger(DEFAULT_LOG_PATH)
    try:
        if not cfg.input_image.exists():
            raise PipelineError(f"Входное изображение не найдено: {cfg.input_image}")
        pipeline = ComfyVideoPipeline(cfg)
        pipeline.run()
        print(f"[OK] Готово: {cfg.output_video}")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.log("Pipeline error", f"Ошибка: {exc}")
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
