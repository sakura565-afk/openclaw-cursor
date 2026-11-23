"""Сборка API-графов ComfyUI для мебельного inpaint (по образцу qwen_masked_fix)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import MachineProfile, ModelBundle

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS_DIR = _REPO_ROOT / "comfyui_workflows"

NEG_FURNITURE = (
    "deformed furniture, melted legs, extra legs, missing legs, broken seams, "
    "warped silhouette, cartoon, illustration, watermark, text, blurry, "
    "wrong proportions, floating cushions"
)

# Negative для перекраски ткани (рецепт-эталон из ТЗ FURN-SRC-20261122).
# Без слов "beige/grey/white" модель оставляет исходный бежевый цвет обивки —
# замерено: SDXL Inpaint при denoise 0.85 дал B-R -17.2 вместо ожидаемого +40.
NEG_RECOLOR = (
    "glossy, shiny, plastic, leather, latex, grey, black, white, monochrome, "
    "beige, tan, cream, floral print, pillows, blurry, distorted, watermark, "
    "text, wooden legs, lowres"
)


def _qwen_edit_names(models: ModelBundle) -> tuple[str, str, str]:
    return (
        models.unet or os.environ.get("QWEN_EDIT_MODEL", "qwen_image_edit_2509_fp8_e4m3fn.safetensors"),
        models.clip or os.environ.get("QWEN_EDIT_CLIP", "qwen_2.5_vl_7b_fp8_scaled.safetensors"),
        models.vae or os.environ.get("QWEN_EDIT_VAE", "qwen_image_vae_2d.safetensors"),
    )


def build_qwen_masked_inpaint_graph(
    *,
    src_name: str,
    mask_name: str,
    positive: str,
    profile: MachineProfile,
    seed: int = 0,
    denoise: float = 0.52,
    steps: int = 24,
    grow_mask_by: int = 10,
    filename_prefix: str = "furniture_qwen",
) -> dict[str, Any]:
    """Qwen VAEEncodeForInpaint — сохраняет силуэт вне маски."""
    unet, clip, vae = _qwen_edit_names(profile.models)
    cfg = float(os.environ.get("FURNITURE_QWEN_CFG", os.environ.get("QWEN_EDIT_CFG", "1.0")))
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": clip, "type": "qwen_image", "device": "default"},
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "5": {"class_type": "LoadImage", "inputs": {"image": src_name}},
        "6": {"class_type": "LoadImage", "inputs": {"image": mask_name}},
        "7": {"class_type": "ImageToMask", "inputs": {"image": ["6", 0], "channel": "red"}},
        "8": {
            "class_type": "VAEEncodeForInpaint",
            "inputs": {
                "pixels": ["5", 0],
                "vae": ["3", 0],
                "mask": ["7", 0],
                "grow_mask_by": grow_mask_by,
            },
        },
        "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": positive}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": NEG_FURNITURE}},
        "11": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": denoise,
                "positive": ["9", 0],
                "negative": ["10", 0],
                "latent_image": ["8", 0],
            },
        },
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {
            "class_type": "SaveImage",
            "inputs": {"images": ["12", 0], "filename_prefix": filename_prefix},
        },
    }


def build_checkpoint_inpaint_graph(
    *,
    src_name: str,
    mask_name: str,
    positive: str,
    profile: MachineProfile,
    seed: int = 0,
    denoise: float = 0.85,
    steps: int = 32,
    cfg: float = 7.0,
    sampler_name: str = "dpmpp_2m",
    scheduler: str = "karras",
    grow_mask_by: int = 16,
    ckpt_override: str | None = None,
    negative: str | None = None,
    filename_prefix: str = "furniture_sdxl_inpaint",
) -> dict[str, Any]:
    """Классический SDXL inpaint (Juggernaut XL Inpaint).

    Рецепт-эталон из ТЗ FURN-SRC-20261122 (замерено на M2, RTX 3060 12 ГБ):
      checkpoint juggernautXL_versionXInpaint.safetensors (SDXL)
      sampler dpmpp_2m / karras, steps 32, cfg 7.0, denoise 0.85
      VAEEncodeForInpaint + ImageCompositeMasked(destination=source)
    ImageCompositeMasked склеивает сгенерированное с оригиналом по маске —
    вне маски остаётся исходный пиксель.
    """
    ckpt = ckpt_override or profile.models.checkpoint or "juggernautXL_versionXInpaint.safetensors"
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "5": {"class_type": "LoadImage", "inputs": {"image": src_name}},
        "6": {"class_type": "LoadImage", "inputs": {"image": mask_name}},
        "7": {"class_type": "ImageToMask", "inputs": {"image": ["6", 0], "channel": "red"}},
        "8": {
            "class_type": "VAEEncodeForInpaint",
            "inputs": {"pixels": ["5", 0], "vae": ["1", 2], "mask": ["7", 0], "grow_mask_by": grow_mask_by},
        },
        "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": positive}},
        "10": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": negative or NEG_RECOLOR},
        },
        "11": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": denoise,
                "positive": ["9", 0],
                "negative": ["10", 0],
                "latent_image": ["8", 0],
            },
        },
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["1", 2]}},
        # Склейка с оригиналом по маске: вне маски остаётся исходный пиксель.
        "14": {
            "class_type": "ImageCompositeMasked",
            "inputs": {
                "destination": ["5", 0],
                "source": ["12", 0],
                "x": 0,
                "y": 0,
                "resize_source": False,
            },
        },
        "13": {
            "class_type": "SaveImage",
            "inputs": {"images": ["14", 0], "filename_prefix": filename_prefix},
        },
    }


def build_flux_fill_gguf_graph(
    *,
    src_name: str,
    mask_name: str,
    positive: str,
    profile: MachineProfile,
    seed: int = 0,
    denoise: float = 1.0,
    steps: int = 20,
    cfg: float = 3.5,
    negative: str | None = None,
    filename_prefix: str = "furniture_flux_fill",
) -> dict[str, Any]:
    """Flux Fill через UnetLoaderGGUF (нужны ComfyUI-GGUF и x-flux на хосте).

    ВАЖНО: используется нативный для Flux Fill пайплайн ComfyUI
    InpaintConditionEncode -> InpaintConditionApply. Старый InpaintModelConditioning
    с этой моделью даёт пустой результат: обивка не меняется (B-R в маске
    -37.6 против -38.5 у референса) при любом denoise.
    Маска: белое = перекрашить, чёрное = сохранить.
    """
    gguf = profile.models.flux_fill_gguf or "flux1-fill-dev-Q8_0.gguf"
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": gguf}},
        "2": {
            "class_type": "DualCLIPLoader",
            "inputs": {
                "clip_name1": os.environ.get("FLUX_CLIP_L", "clip_l.safetensors"),
                # Имя T5 должно существовать в models ComfyUI: t5xxl_fp16 на
                # M1 нет, лежит только t5xxl_fp8_e4m3fn_scaled.
                "clip_name2": os.environ.get(
                    "FLUX_CLIP_T5", "t5xxl_fp8_e4m3fn_scaled.safetensors"
                ),
                "type": "flux",
            },
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": os.environ.get("FLUX_VAE", "ae.safetensors")}},
        "5": {"class_type": "LoadImage", "inputs": {"image": src_name}},
        "6": {"class_type": "LoadImage", "inputs": {"image": mask_name}},
        "7": {"class_type": "ImageToMask", "inputs": {"image": ["6", 0], "channel": "red"}},
        "14": {
            "class_type": "InpaintConditionEncode",
            "inputs": {"vae": ["3", 0], "pixels": ["5", 0], "mask": ["7", 0]},
        },
        "8": {
            "class_type": "InpaintConditionApply",
            "inputs": {
                "positive": ["9", 0],
                "negative": ["10", 0],
                "inpaint_condition": ["14", 0],
                "noise_mask": True,
            },
        },
        "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": positive}},
        "10": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["2", 0], "text": negative or NEG_RECOLOR},
        },
        "11": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": denoise,
                "positive": ["8", 0],
                "negative": ["8", 1],
                "latent_image": ["8", 2],
            },
        },
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {
            "class_type": "SaveImage",
            "inputs": {"images": ["12", 0], "filename_prefix": filename_prefix},
        },
    }


def load_ui_workflow_api_path(path: Path) -> dict[str, Any]:
    """Загрузить уже API-формат JSON (dict node_id -> class_type/inputs)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if "nodes" in data:
        raise ValueError(
            f"{path} в UI-формате; сконвертируйте через scripts/comfy_ui_to_api.py"
        )
    return data


def workflow_path_candidates() -> dict[str, Path]:
    """Файлы из comfyui_workflows/, пригодные как шаблоны (face/klein — не мебель)."""
    names = {
        "flux_klein_face_swap": _WORKFLOWS_DIR / "flux_klein_face_swap.json",
        "flux_klein_face_swap_gguf": _WORKFLOWS_DIR / "flux_klein_face_swap_gguf.json",
        "face_swap_v1_klein_bfs": _WORKFLOWS_DIR / "face_swap_v1_klein_bfs.json",
    }
    return {k: v for k, v in names.items() if v.is_file()}