"""Профили ComfyUI и имена моделей с fallback через переменные окружения.

Реальные пути на M1/M2 не зашиты как единственный источник правды: задаются
через env или task.json, здесь только разумные значения по умолчанию.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value if value else default


@dataclass(frozen=True)
class ModelBundle:
    """Имена checkpoint/UNET/VAE/CLIP как они лежат в папке models ComfyUI."""

    unet: str = ""
    clip: str = ""
    vae: str = ""
    checkpoint: str = ""
    flux_fill_gguf: str = ""
    notes: str = ""


# DESKTOP-J25T962, RTX 4070 Ti SUPER 16 ГБ
M1_MODELS = ModelBundle(
    unet=_env("FURNITURE_M1_QWEN_UNET", "qwen_image_2.1_int8_convrot.safetensors"),
    clip=_env("FURNITURE_M1_QWEN_CLIP", "qwen_2.5_vl_7b_fp8_scaled.safetensors"),
    vae=_env("FURNITURE_M1_QWEN_VAE", "qwen_image_vae_2d.safetensors"),
    checkpoint=_env(
        "FURNITURE_M1_CHECKPOINT",
        "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors",
    ),
    flux_fill_gguf=_env("FURNITURE_M1_FLUX_FILL", "flux1-fill-dev-Q8_0.gguf"),
    notes="M1 primary: Qwen 2.1 masked edit или Juggernaut XL; Flux Fill GGUF Q8",
)

# DESKTOP-6ECJBT6, RTX 3060 12 ГБ (доступ с M1 через Hermes Funnel)
M2_MODELS = ModelBundle(
    unet=_env("FURNITURE_M2_QWEN_UNET", "qwen_image_fp8_e4m3fn.safetensors"),
    clip=_env("FURNITURE_M2_QWEN_CLIP", "qwen_2.5_vl_7b_fp8_scaled.safetensors"),
    vae=_env("FURNITURE_M2_QWEN_VAE", "qwen_image_vae_2d.safetensors"),
    checkpoint=_env(
        "FURNITURE_M2_CHECKPOINT",
        "juggernautXL_versionXInpaint.safetensors",
    ),
    flux_fill_gguf=_env("FURNITURE_M2_FLUX_FILL", "flux1-fill-dev-Q4_K_S.gguf"),
    notes="M2: Qwen fp8, Juggernaut XL Inpaint, BrushNet/LanPaint — через custom nodes",
)


@dataclass(frozen=True)
class MachineProfile:
    """Куда слать HTTP API ComfyUI."""

    name: str
    comfy_url: str
    models: ModelBundle
    extra: dict[str, Any] = field(default_factory=dict)


def default_profiles() -> dict[str, MachineProfile]:
    return {
        "local": MachineProfile(
            name="local",
            comfy_url=_env("FURNITURE_COMFY_URL", _env("COMFYUI_PROD_URL", "http://127.0.0.1:8188")),
            models=M1_MODELS,
        ),
        "m1": MachineProfile(
            name="m1",
            comfy_url=_env("FURNITURE_COMFY_M1_URL", "http://127.0.0.1:8188"),
            models=M1_MODELS,
        ),
        "m2": MachineProfile(
            name="m2",
            comfy_url=_env(
                "FURNITURE_COMFY_M2_URL",
                _env("COMFYUI_M2_URL", "https://DESKTOP-6ECJBT6.example:8188"),
            ),
            models=M2_MODELS,
            extra={"peer": "hermes_funnel"},
        ),
        "remote-peer": MachineProfile(
            name="remote-peer",
            comfy_url=_env("FURNITURE_COMFY_PEER_URL", _env("FURNITURE_COMFY_M2_URL", "")),
            models=M2_MODELS,
        ),
    }


def resolve_profile(host: str | None) -> MachineProfile:
    """host: local|m1|m2|remote-peer или полный URL http(s)://."""
    profiles = default_profiles()
    if not host:
        return profiles["local"]
    key = host.strip().lower()
    if key in profiles:
        profile = profiles[key]
        if key in ("m2", "remote-peer") and not profile.comfy_url:
            raise ValueError(
                "Для M2 задайте FURNITURE_COMFY_M2_URL (HTTPS Funnel с Hermes peer)."
            )
        return profile
    if key.startswith("http://") or key.startswith("https://"):
        return MachineProfile(name="custom", comfy_url=key.rstrip("/"), models=M1_MODELS)
    raise ValueError(f"Неизвестный comfy host: {host!r}")