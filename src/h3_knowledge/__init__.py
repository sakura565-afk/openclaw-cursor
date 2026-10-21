"""База знаний по MiniMax H3 I2V: проверенные константы и валидаторы LoRA.

Модуль переносит в репозиторий знания, которые раньше жили только в локальном
скилле на одной машине. Всё, что лежит здесь, проверено реальными рендерами на
хосте (RTX 3060 12 GB, ComfyUI на порту 8188) — см. комментарии-источники в
``constants.py`` и каталог граблей в ``pitfalls.md``.

Только stdlib: модуль обязан импортироваться в любом окружении, включая
облачного агента без установленных ML-зависимостей.
"""

from __future__ import annotations

from src.h3_knowledge.constants import (
    AR_TOLERANCE,
    DEFAULT_SAMPLER,
    DEFAULT_SHIFT_AUDIO,
    DEFAULT_SHIFT_VIDEO,
    DEFAULT_STEPS,
    H3_COMPATIBLE_LORA_KEY_PREFIX,
    KNOWN_LORAS,
    RESOLUTIONS,
    VALID_LENGTHS,
    Resolution,
    aspect_ratio,
    find_resolution,
    is_valid_length,
    length_to_seconds,
    nearest_valid_length,
)
from src.h3_knowledge.lora_compat import (
    LoraCompat,
    SafetensorsError,
    build_safetensors_header_bytes,
    check_lora_compatibility,
    list_loras_in_dir,
    read_safetensors_header,
)

__all__ = [
    "AR_TOLERANCE",
    "DEFAULT_SAMPLER",
    "DEFAULT_SHIFT_AUDIO",
    "DEFAULT_SHIFT_VIDEO",
    "DEFAULT_STEPS",
    "H3_COMPATIBLE_LORA_KEY_PREFIX",
    "KNOWN_LORAS",
    "RESOLUTIONS",
    "VALID_LENGTHS",
    "LoraCompat",
    "Resolution",
    "SafetensorsError",
    "aspect_ratio",
    "build_safetensors_header_bytes",
    "check_lora_compatibility",
    "find_resolution",
    "is_valid_length",
    "length_to_seconds",
    "list_loras_in_dir",
    "nearest_valid_length",
    "read_safetensors_header",
]
