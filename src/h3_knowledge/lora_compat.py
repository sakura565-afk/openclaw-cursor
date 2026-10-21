"""Определение архитектуры LoRA по заголовку safetensors — без загрузки весов.

Зачем это нужно. В каталоге ``E:\\AI\\models\\comfyui\\loras\\`` лежат десятки
LoRA от разных базовых моделей. На глаз (по имени файла) H3-совместимость не
определяется, и агент раз за разом подключал к H3 чужие LoRA по принципу
«подключим все NSFW подряд»: ComfyUI такие файлы молча принимает, LoRA не
действует, а 20 минут рендера уходят в брак.

Проверено в окружении — следующие LoRA НЕ совместимы с H3 (все они для
Flux/SDXL, ключи ``transformer.*`` / ``text_model.*``):

* ``NSFW_master``
* ``nsfw_flux_lora_v1``
* ``flux2klein_nsfw``
* ``undressing-PN``

Перебирать их для H3 бессмысленно — H3-совместимой является LoRA с ключами
``diffusion_model.blocks.*`` (см. ``constants.H3_COMPATIBLE_LORA_KEY_PREFIX``);
на хосте это NSGIRL и семейство ``h3-*``.

Формат safetensors: первые 8 байт — длина JSON-заголовка (uint64 little-endian),
дальше сам JSON. Этого достаточно, чтобы прочитать имена тензоров, не поднимая
в память гигабайты весов.

Только stdlib.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.h3_knowledge.constants import H3_COMPATIBLE_LORA_KEY_PREFIX

# Защита от битого/чужого файла: заголовок safetensors реально занимает единицы
# мегабайт, поэтому всё, что больше, читать не нужно.
MAX_HEADER_BYTES = 64 * 1024 * 1024

# Ключ служебных метаданных внутри заголовка — это не тензор.
METADATA_KEY = "__metadata__"

# Идентификатор базовой архитектуры H3.
H3_BASE_MODEL = "minimax-h3-fl2va"


class SafetensorsError(ValueError):
    """Файл не является читаемым safetensors (битая длина, не-JSON, обрезан)."""


@dataclass(frozen=True)
class _ArchRule:
    """Правило определения архитектуры по маркеру в имени тензора."""

    marker: str
    substring: bool
    base_model: str
    compatible: bool
    reason: str

    def matches(self, key: str) -> bool:
        return self.marker in key if self.substring else key.startswith(self.marker)


# Порядок важен: при равном количестве совпадений выигрывает правило выше.
ARCHITECTURE_RULES: tuple[_ArchRule, ...] = (
    _ArchRule(
        marker=H3_COMPATIBLE_LORA_KEY_PREFIX,
        substring=False,
        base_model=H3_BASE_MODEL,
        compatible=True,
        reason="ключи diffusion_model.blocks.* — раскладка MiniMax H3 (fl2va)",
    ),
    _ArchRule(
        marker="transformer.single_transformer_blocks.",
        substring=False,
        base_model="flux",
        compatible=False,
        reason="ключи transformer.single_transformer_blocks.* — Flux/SDXL, для H3 несовместима",
    ),
    _ArchRule(
        marker="transformer.transformer_blocks.",
        substring=False,
        base_model="flux",
        compatible=False,
        reason="ключи transformer.transformer_blocks.* — Flux/SDXL, для H3 несовместима",
    ),
    _ArchRule(
        marker="diffusion_model.diffusion_blocks.",
        substring=False,
        base_model="sdxl-or-flux",
        compatible=False,
        reason="ключи diffusion_model.diffusion_blocks.* — SDXL/Flux, для H3 несовместима",
    ),
    _ArchRule(
        marker="lora_unet_",
        substring=False,
        base_model="sdxl",
        compatible=False,
        reason="ключи lora_unet_* — SDXL (kohya-раскладка), для H3 несовместима",
    ),
    _ArchRule(
        marker="text_model",
        substring=True,
        base_model="sdxl-or-flux",
        compatible=False,
        reason="ключи text_model* — текстовый энкодер SDXL/Flux, для H3 несовместима",
    ),
    _ArchRule(
        marker="lora_te",
        substring=False,
        base_model="sdxl",
        compatible=False,
        reason="ключи lora_te* — текстовый энкодер SDXL, для H3 несовместима",
    ),
)


@dataclass
class LoraCompat:
    """Результат проверки одной LoRA на совместимость с H3."""

    path: Path
    name: str
    compatible: bool
    base_model: str | None
    key_prefixes: list[str] = field(default_factory=list)
    tensor_count: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Представление для JSON-вывода CLI."""
        return {
            "path": str(self.path),
            "name": self.name,
            "compatible": self.compatible,
            "base_model": self.base_model,
            "key_prefixes": list(self.key_prefixes),
            "tensor_count": self.tensor_count,
            "reason": self.reason,
        }


def read_safetensors_header(path: str | Path) -> dict[str, Any]:
    """Прочитать JSON-заголовок safetensors, не загружая веса.

    Читает первые 8 байт (длина заголовка, uint64 little-endian), затем ровно
    столько байт JSON. Сами тензоры не трогаются — на 2-гигабайтном файле это
    чтение занимает миллисекунды.

    Raises:
        FileNotFoundError: файла нет.
        SafetensorsError: файл короче 8 байт, длина заголовка бессмысленна,
            заголовок обрезан или не является JSON-объектом.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"LoRA file not found: {file_path}")

    file_size = file_path.stat().st_size
    if file_size < 8:
        raise SafetensorsError(f"{file_path.name}: file is shorter than the 8-byte header length")

    with file_path.open("rb") as handle:
        raw_length = handle.read(8)
        header_length = int.from_bytes(raw_length, byteorder="little", signed=False)

        if header_length == 0:
            raise SafetensorsError(f"{file_path.name}: header length is zero")
        if header_length > MAX_HEADER_BYTES:
            raise SafetensorsError(
                f"{file_path.name}: header length {header_length} exceeds "
                f"the {MAX_HEADER_BYTES} byte limit — not a safetensors file"
            )
        if header_length > file_size - 8:
            raise SafetensorsError(
                f"{file_path.name}: header length {header_length} exceeds file size {file_size}"
            )

        raw_header = handle.read(header_length)

    if len(raw_header) != header_length:
        raise SafetensorsError(f"{file_path.name}: header is truncated")

    try:
        header = json.loads(raw_header.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SafetensorsError(f"{file_path.name}: header is not valid JSON ({exc})") from exc

    if not isinstance(header, dict):
        raise SafetensorsError(f"{file_path.name}: header is {type(header).__name__}, expected object")

    return header


def build_safetensors_header_bytes(header: dict[str, Any]) -> bytes:
    """Собрать байты safetensors-файла с данным заголовком (обратная операция).

    Нужна для фикстур: позволяет сделать крошечный валидный safetensors без
    torch и без гигабайтных весов. Тело файла пустое — заголовок самодостаточен
    для определения архитектуры.
    """
    raw_header = json.dumps(header, ensure_ascii=False).encode("utf-8")
    return len(raw_header).to_bytes(8, byteorder="little", signed=False) + raw_header


def tensor_keys(header: dict[str, Any]) -> list[str]:
    """Вернуть имена тензоров из заголовка (без служебного ``__metadata__``)."""
    return [key for key in header if key != METADATA_KEY]


def key_prefixes(keys: list[str], depth: int = 2) -> list[str]:
    """Свернуть имена тензоров до уникальных префиксов глубины ``depth``."""
    prefixes = {".".join(key.split(".")[:depth]) for key in keys}
    return sorted(prefixes)


def _metadata_architecture(header: dict[str, Any]) -> str | None:
    """Достать архитектуру из ``__metadata__``, если тренер её записал."""
    metadata = header.get(METADATA_KEY)
    if not isinstance(metadata, dict):
        return None
    for key in ("modelspec.architecture", "ss_base_model_version", "base_model"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def check_lora_compatibility(path: str | Path) -> LoraCompat:
    """Определить, подходит ли LoRA к MiniMax H3, по префиксам ключей тензоров.

    Архитектура определяется правилами ``ARCHITECTURE_RULES``: побеждает то,
    под которое попало больше всего ключей. Метаданные тренера используются
    только как подсказка в ``reason`` — им нельзя доверять, они часто врут
    после переконвертации.

    Ошибки чтения не пробрасываются наружу: нечитаемый файл — это тоже ответ
    «несовместима», просто с другой причиной. Валидатору нужен список проблем,
    а не traceback на первом битом файле.
    """
    file_path = Path(path)

    try:
        header = read_safetensors_header(file_path)
    except (FileNotFoundError, SafetensorsError, OSError) as exc:
        return LoraCompat(
            path=file_path,
            name=file_path.name,
            compatible=False,
            base_model=None,
            reason=f"не удалось прочитать заголовок: {exc}",
        )

    keys = tensor_keys(header)
    prefixes = key_prefixes(keys)
    metadata_arch = _metadata_architecture(header)

    if not keys:
        return LoraCompat(
            path=file_path,
            name=file_path.name,
            compatible=False,
            base_model=metadata_arch,
            key_prefixes=prefixes,
            tensor_count=0,
            reason="в заголовке нет тензоров — файл не является LoRA",
        )

    hits = {rule.marker: sum(1 for key in keys if rule.matches(key)) for rule in ARCHITECTURE_RULES}
    best = max(
        ARCHITECTURE_RULES,
        key=lambda rule: (hits[rule.marker], -ARCHITECTURE_RULES.index(rule)),
    )

    if hits[best.marker] == 0:
        return LoraCompat(
            path=file_path,
            name=file_path.name,
            compatible=False,
            base_model=metadata_arch,
            key_prefixes=prefixes,
            tensor_count=len(keys),
            reason=(
                "архитектура не опознана: нет ни одного известного префикса ключей "
                f"(найдено: {', '.join(prefixes[:5]) or 'ничего'}). "
                f"Для H3 требуется {H3_COMPATIBLE_LORA_KEY_PREFIX}*"
            ),
        )

    reason = best.reason
    if metadata_arch:
        reason = f"{reason}; __metadata__ сообщает '{metadata_arch}'"

    return LoraCompat(
        path=file_path,
        name=file_path.name,
        compatible=best.compatible,
        base_model=best.base_model,
        key_prefixes=prefixes,
        tensor_count=len(keys),
        reason=reason,
    )


def list_loras_in_dir(directory: str | Path, recursive: bool = True) -> list[LoraCompat]:
    """Проверить все ``*.safetensors`` в каталоге, отсортировав по имени.

    Raises:
        FileNotFoundError: каталога нет или это не каталог.
    """
    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"LoRA directory not found: {root}")

    pattern = "**/*.safetensors" if recursive else "*.safetensors"
    files = sorted((p for p in root.glob(pattern) if p.is_file()), key=lambda p: str(p).lower())
    return [check_lora_compatibility(file_path) for file_path in files]
