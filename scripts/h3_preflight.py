#!/usr/bin/env python3
"""Префлайт-валидатор workflow MiniMax H3 I2V — проверка ДО отправки в ComfyUI.

Смысл инструмента: один прогон H3 на RTX 3060 стоит 15-25 минут. Ошибки вроде
невалидной `length`, разрешения вне сетки или подключённой Flux-LoRA ComfyUI
либо молча проглатывает, либо роняет в конце очереди — в обоих случаях время
уже потрачено. Этот скрипт ловит такие ошибки за доли секунды.

Использование::

    python scripts/h3_preflight.py --workflow workflow.json
    python scripts/h3_preflight.py --workflow wf.json --source-image source.png
    python scripts/h3_preflight.py --from-script render_v7.py --lora-dir "E:/AI/models/comfyui/loras"
    python scripts/h3_preflight.py --selftest

Коды выхода: 0 — ошибок нет (предупреждения не влияют), 1 — есть ERRORS,
2 — не удалось прочитать вход. Traceback наружу не выпускается.

Только stdlib.
"""

from __future__ import annotations

import argparse
import ast
import json
import struct
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, TextIO, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:  # запуск вида `python scripts/h3_preflight.py`
    sys.path.insert(0, str(_REPO_ROOT))

from src.h3_knowledge import constants as C  # noqa: E402
from src.h3_knowledge.lora_compat import (  # noqa: E402
    build_safetensors_header_bytes,
    check_lora_compatibility,
)

# Узлы H3, из которых читаются length / width / height / промты.
H3_NODE_CLASSES = (
    "MiniMaxH3ImageToVideo",
    "MiniMaxH3TextToVideo",
    "MiniMaxH3ImageToVideoAdvanced",
)

# Узлы подключения LoRA.
LORA_NODE_CLASSES = (
    "LoraLoader",
    "LoraLoaderModelOnly",
    "LoraTagLoader",
)

# Узлы текстовых промтов для workflow, где промт не лежит прямо в узле H3.
TEXT_ENCODE_CLASSES = ("CLIPTextEncode", "CLIPTextEncodeSDXL")

# Маркеры негативного промта в заголовке узла.
NEGATIVE_TITLE_MARKERS = ("negative", "негатив")


@dataclass
class PreflightResult:
    """Накопленные ошибки и предупреждения одной проверки."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def exit_code(self) -> int:
        return 0 if self.ok else 1


class WorkflowLoadError(Exception):
    """Не удалось получить workflow из файла или скрипта."""


# --------------------------------------------------------------------------- #
# Загрузка workflow
# --------------------------------------------------------------------------- #


def _is_unresolved(value: Any) -> bool:
    """Значение не удалось вычислить статически (например, вызов random)."""
    return isinstance(value, _Unresolved)


def _looks_like_workflow(candidate: Any) -> bool:
    """Похоже ли это на ComfyUI API-workflow (словарь узлов с ``class_type``)."""
    if not isinstance(candidate, dict) or not candidate:
        return False
    nodes = [value for value in candidate.values() if isinstance(value, dict)]
    if len(nodes) != len(candidate):
        return False
    return any("class_type" in node for node in nodes)


def load_workflow_file(path: str | Path) -> dict[str, Any]:
    """Прочитать workflow из JSON-файла."""
    file_path = Path(path)
    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowLoadError(f"не удалось прочитать {file_path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkflowLoadError(f"{file_path}: невалидный JSON ({exc})") from exc

    if isinstance(data, dict) and isinstance(data.get("prompt"), dict):
        data = data["prompt"]  # формат /prompt-запроса целиком

    if isinstance(data, dict) and isinstance(data.get("nodes"), list):
        raise WorkflowLoadError(
            f"{file_path}: это UI-формат workflow. Нужен API-формат "
            "(Workflow -> Export (API) в ComfyUI)."
        )

    if not _looks_like_workflow(data):
        raise WorkflowLoadError(f"{file_path}: не похоже на ComfyUI API-workflow")

    return data



def _collect_module_symbols(tree: ast.Module) -> dict[str, Any]:
    """Собрать имя -> значение для простых присваиваний уровня модуля.

    Поддерживаются константы (str/int/float/bool/None), списки, кортежи и словари
    из таких констант. Выражения с вызовами игнорируются — выполнять скрипт нельзя.
    """
    symbols: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = _eval_node(node.value, symbols)
        if value is not _UNRESOLVED:
            symbols[target.id] = value
    return symbols


class _Unresolved:
    """Маркер значения, которое не удалось вычислить без выполнения кода."""

    def __repr__(self) -> str:  # pragma: no cover - только для отладки
        return "<unresolved>"


_UNRESOLVED = _Unresolved()


def _eval_node(node: ast.AST, symbols: dict[str, Any]) -> Any:
    """Вычислить узел AST, подставляя Name из символьной таблицы.

    Всё, что требует выполнения кода (вызовы функций, f-строки с вызовами,
    атрибуты), помечается как неразрешённое и в workflow не попадает.
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in symbols:
            return symbols[node.id]
        return _UNRESOLVED
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        items = []
        for element in node.elts:
            value = _eval_node(element, symbols)
            if value is _UNRESOLVED:
                return _UNRESOLVED
            items.append(value)
        if isinstance(node, ast.Tuple):
            return tuple(items)
        if isinstance(node, ast.Set):
            return set(items)
        return items
    if isinstance(node, ast.Dict):
        result: dict[Any, Any] = {}
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:
                return _UNRESOLVED
            key = _eval_node(key_node, symbols)
            value = _eval_node(value_node, symbols)
            if key is _UNRESOLVED:
                return _UNRESOLVED
            # Неразрешённое значение (например random.randint для seed) НЕ
            # обнуляет весь словарь: оставляем маркер, проверки его пропустят.
            if value is _UNRESOLVED:
                value = _UNRESOLVED
            result[key] = value
        return result
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        operand = _eval_node(node.operand, symbols)
        if operand is _UNRESOLVED or not isinstance(operand, (int, float)):
            return _UNRESOLVED
        return -operand if isinstance(node.op, ast.USub) else +operand
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        left = _eval_node(node.left, symbols)
        right = _eval_node(node.right, symbols)
        if left is _UNRESOLVED or right is _UNRESOLVED:
            return _UNRESOLVED
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            return left / right
        except (TypeError, ZeroDivisionError):
            return _UNRESOLVED
    if isinstance(node, ast.JoinedStr):
        # f-строка: собираем из статических частей и константных подстановок.
        parts: list[str] = []
        for value_node in node.values:
            if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
                parts.append(value_node.value)
                continue
            if isinstance(value_node, ast.FormattedValue):
                inner = _eval_node(value_node.value, symbols)
                if inner is _UNRESOLVED or not isinstance(inner, (str, int, float)):
                    return _UNRESOLVED
                parts.append(str(inner))
                continue
            return _UNRESOLVED
        return "".join(parts)
    return _UNRESOLVED


def extract_workflow_from_script(path: str | Path) -> dict[str, Any]:
    """Вытащить зашитый в python-скрипт workflow.

    Скрипты рендера часто держат workflow прямо в коде — как dict-литерал или
    как JSON в строке. Разбор идёт через ``ast``: исполнять чужой скрипт ради
    получения словаря нельзя. Берётся самый большой подходящий литерал.
    """
    file_path = Path(path)
    try:
        source = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowLoadError(f"не удалось прочитать {file_path}: {exc}") from exc

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise WorkflowLoadError(f"{file_path}: не разбирается как python ({exc})") from exc

    # Символьная таблица модуля: имя -> значение для простых присваиваний.
    # Реальные скрипты рендера собирают workflow из переменных
    # (``"prompt": prompt, "width": width``), поэтому одного ast.literal_eval
    # мало: он падает на любом Name-ссылке.
    symbols = _collect_module_symbols(tree)

    candidates: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            value = _eval_node(node, symbols)
            if not isinstance(value, dict):
                continue
            if _looks_like_workflow(value):
                candidates.append(value)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            if not text.startswith("{"):
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            if _looks_like_workflow(value):
                candidates.append(value)

    if not candidates:
        raise WorkflowLoadError(f"{file_path}: в скрипте не найден workflow (dict или JSON-строка)")

    return max(candidates, key=len)


# --------------------------------------------------------------------------- #
# Разбор workflow
# --------------------------------------------------------------------------- #


def iter_nodes(workflow: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Пройти по узлам workflow, пропуская мусорные значения."""
    for node_id, node in workflow.items():
        if isinstance(node, dict):
            yield str(node_id), node


def _inputs(node: dict[str, Any]) -> dict[str, Any]:
    inputs = node.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def _title(node: dict[str, Any]) -> str:
    meta = node.get("_meta")
    if isinstance(meta, dict):
        title = meta.get("title")
        if isinstance(title, str):
            return title
    return ""


def find_h3_nodes(workflow: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Найти узлы H3 (их может быть несколько в многосценных workflow)."""
    return [
        (node_id, node)
        for node_id, node in iter_nodes(workflow)
        if node.get("class_type") in H3_NODE_CLASSES
    ]


def resolve_dimension(workflow: dict[str, Any], value: Any, name: str) -> int | None:
    """Развернуть width/height: литерал или ссылка на upstream-узел.

    Ссылка вида ``["6", 0]`` разворачивается только если в upstream-узле лежит
    литеральное поле с тем же именем. ``ResolutionSelector`` считает размер
    внутри себя, поэтому статически он неразрешим — возвращается ``None``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, list) and len(value) == 2:
        upstream = workflow.get(str(value[0]))
        if isinstance(upstream, dict):
            candidate = _inputs(upstream).get(name)
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                return candidate
    return None


def collect_prompts(workflow: dict[str, Any]) -> tuple[str, str]:
    """Собрать позитивный и негативный промты из workflow."""
    positives: list[str] = []
    negatives: list[str] = []

    for _node_id, node in iter_nodes(workflow):
        class_type = node.get("class_type")
        inputs = _inputs(node)

        if class_type in H3_NODE_CLASSES:
            for key in ("prompt", "positive_prompt"):
                if isinstance(inputs.get(key), str):
                    positives.append(inputs[key])
            for key in ("negative_prompt", "negative"):
                if isinstance(inputs.get(key), str):
                    negatives.append(inputs[key])
        elif class_type in TEXT_ENCODE_CLASSES:
            text = inputs.get("text")
            if not isinstance(text, str):
                continue
            title = _title(node).lower()
            if any(marker in title for marker in NEGATIVE_TITLE_MARKERS):
                negatives.append(text)
            else:
                positives.append(text)

    return "\n".join(positives), "\n".join(negatives)


def collect_shifts(workflow: dict[str, Any]) -> dict[str, float]:
    """Найти shift_video / shift_audio в любом узле, который их объявляет."""
    shifts: dict[str, float] = {}
    for _node_id, node in iter_nodes(workflow):
        for key, value in _inputs(node).items():
            if key in ("shift_video", "shift_audio") and isinstance(value, (int, float)):
                if not isinstance(value, bool):
                    shifts[key] = float(value)
    return shifts


# --------------------------------------------------------------------------- #
# Размер исходника (stdlib, без Pillow)
# --------------------------------------------------------------------------- #


def read_image_size(path: str | Path) -> tuple[int, int]:
    """Прочитать размер PNG/JPEG из заголовка, не декодируя пиксели.

    Pillow сюда тянуть нельзя (ограничение «только stdlib»), а AR исходника
    нужен для проверки сетки разрешений.
    """
    file_path = Path(path)
    data = file_path.read_bytes()

    if data[:8] == b"\x89PNG\r\n\x1a\n":
        if len(data) < 24 or data[12:16] != b"IHDR":
            raise WorkflowLoadError(f"{file_path.name}: битый PNG-заголовок")
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)

    if data[:2] == b"\xff\xd8":
        offset = 2
        while offset + 9 < len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                offset += 2
                continue
            segment_length = struct.unpack(">H", data[offset + 2 : offset + 4])[0]
            # SOF0..SOF15, кроме DHT(C4)/JPG(C8)/DAC(CC) — в них лежат размеры.
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
                return int(width), int(height)
            offset += 2 + segment_length
        raise WorkflowLoadError(f"{file_path.name}: в JPEG не найден SOF-сегмент")

    raise WorkflowLoadError(
        f"{file_path.name}: поддерживаются только PNG и JPEG. "
        "Для других форматов задайте --source-size WxH или --source-ar."
    )


def parse_source_size(text: str) -> tuple[int, int]:
    """Разобрать строку вида ``896x1280``."""
    parts = text.lower().replace("*", "x").split("x")
    if len(parts) != 2:
        raise WorkflowLoadError(f"--source-size: ожидался формат WxH, получено {text!r}")
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise WorkflowLoadError(f"--source-size: не число в {text!r}") from exc
    if width <= 0 or height <= 0:
        raise WorkflowLoadError(f"--source-size: размеры должны быть положительными ({text!r})")
    return width, height


# --------------------------------------------------------------------------- #
# Проверки
# --------------------------------------------------------------------------- #


def _check_length(result: PreflightResult, node_id: str, inputs: dict[str, Any]) -> None:
    """Длина клипа должна лежать в проверенной сетке H3."""
    length = inputs.get("length")
    if length is None:
        result.error(f"узел {node_id}: не задан 'length'")
        return
    if _is_unresolved(length):
        result.warn(
            f"узел {node_id}: 'length' не удалось вычислить статически — "
            "проверка длины пропущена"
        )
        return
    if not C.is_valid_length(length):
        valid = ", ".join(
            f"{value} ({C.length_to_seconds(value)}s)" for value in C.VALID_LENGTHS
        )
        if isinstance(length, int) and not isinstance(length, bool):
            nearest = C.nearest_valid_length(length)
            hint = f" Ближайшая валидная: {nearest} ({C.length_to_seconds(nearest)}s)."
        else:
            hint = ""
        result.error(
            f"узел {node_id}: length={length!r} вне сетки H3. Валидные длины: {valid}.{hint}"
        )


def _check_resolution(
    result: PreflightResult,
    workflow: dict[str, Any],
    node_id: str,
    inputs: dict[str, Any],
    source_ar: float | None,
) -> None:
    """Разрешение должно быть из сетки, а его AR — совпадать с AR исходника."""
    width = resolve_dimension(workflow, inputs.get("width"), "width")
    height = resolve_dimension(workflow, inputs.get("height"), "height")

    if width is None or height is None:
        result.warn(
            f"узел {node_id}: разрешение задано ссылкой на другой узел и не "
            "вычисляется статически — проверка сетки и AR пропущена"
        )
        return

    resolution = C.find_resolution(width, height)
    if resolution is None:
        result.error(
            f"узел {node_id}: разрешение {width}x{height} вне проверенной сетки. "
            f"Проверенные: {C.format_resolution_grid()}"
        )
        return

    if source_ar is None:
        result.warn(
            "AR исходника не задан (--source-image / --source-size / --source-ar) — "
            "сверка AR выходного разрешения с исходником пропущена"
        )
        return

    drift = abs(resolution.aspect_ratio - source_ar)
    if drift > C.AR_TOLERANCE:
        matching = C.resolutions_for_ar(source_ar)
        suggestion = (
            ", ".join(f"{res.width}x{res.height}" for res in matching)
            if matching
            else "в сетке нет разрешения с таким AR — подрежьте исходник"
        )
        result.error(
            f"узел {node_id}: AR выхода {resolution.aspect_ratio:.4f} "
            f"({width}x{height}) расходится с AR исходника {source_ar:.4f} "
            f"на {drift:.4f} при допуске {C.AR_TOLERANCE}. Подходит: {suggestion}"
        )


def _check_shifts(result: PreflightResult, workflow: dict[str, Any]) -> None:
    """shift_video / shift_audio должны лежать в проверенных диапазонах."""
    shifts = collect_shifts(workflow)
    bands = {
        "shift_video": (C.SHIFT_VIDEO_HARD_RANGE, C.SHIFT_VIDEO_VERIFIED_RANGE, C.DEFAULT_SHIFT_VIDEO),
        "shift_audio": (C.SHIFT_AUDIO_HARD_RANGE, C.SHIFT_AUDIO_VERIFIED_RANGE, C.DEFAULT_SHIFT_AUDIO),
    }
    for name, (hard, verified, default) in bands.items():
        if name not in shifts:
            continue
        value = shifts[name]
        if not hard[0] <= value <= hard[1]:
            result.error(
                f"{name}={value} вне допустимого диапазона {hard[0]}..{hard[1]} "
                f"(значение по умолчанию {default})"
            )
        elif not verified[0] <= value <= verified[1]:
            result.warn(
                f"{name}={value} вне проверенной полосы {verified[0]}..{verified[1]} "
                f"(по умолчанию {default}) — результат на этом стеке не замерялся"
            )


def _check_loras(
    result: PreflightResult,
    workflow: dict[str, Any],
    lora_dirs: Sequence[Path] | None,
    positive: str,
) -> None:
    """Каждая LoRA должна существовать и быть H3-совместимой."""
    used: list[str] = []

    for node_id, node in iter_nodes(workflow):
        if node.get("class_type") not in LORA_NODE_CLASSES:
            continue
        inputs = _inputs(node)
        lora_name = inputs.get("lora_name")
        if not isinstance(lora_name, str) or not lora_name.strip():
            result.error(f"узел {node_id}: в LoRA-узле не задан 'lora_name'")
            continue

        used.append(lora_name)
        strength = inputs.get("strength_model")
        if isinstance(strength, (int, float)) and not isinstance(strength, bool):
            if float(strength) == 0.0:
                result.warn(f"узел {node_id}: {lora_name} подключена с strength_model=0 (не действует)")

        if not lora_dirs:
            # lora_dirs может быть None (каталоги недоступны) или пустым списком.
            result.warn(
                f"узел {node_id}: каталог LoRA недоступен — файл {lora_name} "
                "и его совместимость с H3 не проверены (укажите --lora-dir)"
            )
            continue

        # LoRA разложены по двум каталогам, и ComfyUI принимает имя файла,
        # а не путь, — ищем по всем доступным каталогам.
        lora_path = next(
            (
                d / lora_name
                for d in lora_dirs
                if d is not None and (d / lora_name).is_file()
            ),
            None,
        )
        if lora_path is None:
            searched = ", ".join(str(d) for d in lora_dirs)
            result.error(
                f"узел {node_id}: файл LoRA не найден: {lora_name} "
                f"(искали в: {searched})"
            )
            continue

        compat = check_lora_compatibility(lora_path)
        if not compat.compatible:
            result.error(
                f"узел {node_id}: {lora_name} несовместима с H3 "
                f"(base_model={compat.base_model or 'unknown'}): {compat.reason}"
            )

    _check_known_lora_combinations(result, used, positive)


def _check_known_lora_combinations(
    result: PreflightResult, used: list[str], positive: str
) -> None:
    """Сверить набор LoRA с каталогом известных конфликтов и триггеров."""
    used_names = {Path(name).name for name in used}
    positive_lower = positive.lower()

    for name in sorted(used_names):
        known = C.KNOWN_LORAS.get(name)
        if known is None:
            continue

        for conflicting in known.get("avoid_with", ()):
            if conflicting in used_names and name < conflicting:
                result.warn(
                    f"{name} и {conflicting} подключены вместе: проверено, что эта пара "
                    "ухудшает анатомию и не чинит кожу (pitfalls.md, пункт 6)"
                )

        trigger = known.get("trigger")
        if trigger and trigger.lower() not in positive_lower:
            result.warn(
                f"{name} подключена, но триггер '{trigger}' отсутствует в позитивном "
                "промте — LoRA фактически не включится"
            )


def _check_camera_conflict(result: PreflightResult, positive: str, negative: str) -> None:
    """Поймать негатив, который запрещает запрошенное движение камеры."""
    positive_lower = positive.lower()
    negative_lower = negative.lower()

    wanted = [marker for marker in C.CAMERA_PUSH_IN_MARKERS if marker in positive_lower]
    if not wanted:
        return

    blocked = [term for term in C.CAMERA_NEGATIVE_CONFLICT_TERMS if term in negative_lower]
    if not blocked:
        return

    result.warn(
        f"конфликт камеры: позитив просит движение ({', '.join(sorted(set(wanted)))}), "
        f"а негатив его запрещает ({', '.join(sorted(set(blocked)))}). "
        f"Запрещайте обратное движение: {C.RECOMMENDED_CAMERA_NEGATIVE}"
    )


def _check_last_frame(result: PreflightResult, node_id: str, inputs: dict[str, Any], positive: str) -> None:
    """Напомнить про двухточечный якорь, если бриф требует смены позы."""
    if "last_frame" not in inputs or inputs.get("last_frame") is None:
        return
    pose_markers = ("spread", "opens her legs", "changes pose", "stands up", "turns over", "sits up")
    positive_lower = positive.lower()
    if any(marker in positive_lower for marker in pose_markers):
        result.warn(
            f"узел {node_id}: задан last_frame, а промт требует смены позы — "
            "двухточечный якорь прижмёт конечную позу к исходной (pitfalls.md, пункт 2)"
        )


def run_preflight(
    workflow: dict[str, Any],
    lora_dirs: Sequence[Path] | None = None,
    source_ar: float | None = None,
) -> PreflightResult:
    """Прогнать все проверки и вернуть накопленные ошибки/предупреждения."""
    result = PreflightResult()

    h3_nodes = find_h3_nodes(workflow)
    if not h3_nodes:
        result.error(
            "в workflow нет узла MiniMax H3 "
            f"(ожидался один из: {', '.join(H3_NODE_CLASSES)})"
        )
        return result

    positive, negative = collect_prompts(workflow)

    for node_id, node in h3_nodes:
        inputs = _inputs(node)
        _check_length(result, node_id, inputs)
        _check_resolution(result, workflow, node_id, inputs, source_ar)
        _check_last_frame(result, node_id, inputs, positive)

    _check_shifts(result, workflow)
    _check_loras(result, workflow, lora_dirs, positive)
    _check_camera_conflict(result, positive, negative)

    return result


# --------------------------------------------------------------------------- #
# Вывод
# --------------------------------------------------------------------------- #


def render_report(result: PreflightResult, label: str = "") -> str:
    """Собрать человекочитаемый отчёт без traceback."""
    lines: list[str] = []
    if label:
        lines.append(f"workflow: {label}")

    if result.errors:
        lines.append(f"ERRORS ({len(result.errors)}):")
        lines.extend(f"  - {message}" for message in result.errors)
    else:
        lines.append("ERRORS (0): нет")

    if result.warnings:
        lines.append(f"WARNINGS ({len(result.warnings)}):")
        lines.extend(f"  - {message}" for message in result.warnings)
    else:
        lines.append("WARNINGS (0): нет")

    lines.append("PREFLIGHT: OK" if result.ok else "PREFLIGHT: FAILED")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #

# Заголовки синтетических LoRA для фикстур: тела нет, архитектура определяется
# по именам ключей — ровно так же, как у настоящих файлов.
_H3_STUB_HEADER = {
    "__metadata__": {"format": "pt"},
    "diffusion_model.blocks.0.attn.to_q.lora_A.weight": {
        "dtype": "F16", "shape": [16, 2048], "data_offsets": [0, 0],
    },
    "diffusion_model.blocks.0.attn.to_q.lora_B.weight": {
        "dtype": "F16", "shape": [2048, 16], "data_offsets": [0, 0],
    },
}

_FLUX_STUB_HEADER = {
    "__metadata__": {"format": "pt"},
    "transformer.single_transformer_blocks.0.attn.to_q.lora_A.weight": {
        "dtype": "F16", "shape": [16, 3072], "data_offsets": [0, 0],
    },
    "transformer.single_transformer_blocks.0.attn.to_q.lora_B.weight": {
        "dtype": "F16", "shape": [3072, 16], "data_offsets": [0, 0],
    },
}

_NSGIRL_LORA = "NSGIRL-MiniMax-H3-LoRA-By-MM744.safetensors"
_FLUX_LORA = "nsfw_flux_lora_v1.safetensors"


def write_stub_lora(path: Path, header: dict[str, Any]) -> Path:
    """Записать минимальный валидный safetensors-файл для фикстур."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_safetensors_header_bytes(header))
    return path


def fixture_valid_workflow() -> dict[str, Any]:
    """Валидный workflow: сетка соблюдена, LoRA H3-совместима, триггер на месте."""
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": C.BASE_MODEL}},
        "2": {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": _NSGIRL_LORA,
                "strength_model": 0.85,
                "strength_clip": 1.0,
                "model": ["1", 0],
            },
        },
        "3": {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {
                "prompt": "nsgirl, photorealistic skin, static hero framing",
                "negative_prompt": "blurry, low quality, camera pull back",
                "width": 896,
                "height": 1280,
                "length": 175,
                "shift_video": C.DEFAULT_SHIFT_VIDEO,
                "shift_audio": C.DEFAULT_SHIFT_AUDIO,
            },
        },
    }


def fixture_invalid_workflow() -> dict[str, Any]:
    """Невалидный workflow: битая длина, разрешение вне сетки, Flux-LoRA, шифт за границей."""
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": C.BASE_MODEL}},
        "2": {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": _FLUX_LORA,
                "strength_model": 0.8,
                "strength_clip": 1.0,
                "model": ["1", 0],
            },
        },
        "3": {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {
                "prompt": "slow push in on the subject",
                "negative_prompt": "camera pan, camera zoom",
                "width": 900,
                "height": 1300,
                "length": 200,
                "shift_video": 40.0,
                "shift_audio": C.DEFAULT_SHIFT_AUDIO,
            },
        },
    }


def fixture_camera_conflict_workflow() -> dict[str, Any]:
    """Всё валидно, кроме негатива, который запрещает запрошенный наезд."""
    workflow = fixture_valid_workflow()
    workflow["3"]["inputs"]["prompt"] = "nsgirl, slow push in, camera moves closer"
    workflow["3"]["inputs"]["negative_prompt"] = "blurry, camera pan, camera zoom"
    return workflow


def run_selftest(stream: TextIO | None = None) -> int:
    """Прогнать встроенные фикстуры и напечатать результат по каждой.

    Возвращает 0, если все фикстуры повели себя ожидаемо, иначе 1.
    """
    out = stream or sys.stdout
    source_ar = C.aspect_ratio(896, 1280)

    with tempfile.TemporaryDirectory(prefix="h3_preflight_selftest_") as tmp:
        lora_dir = Path(tmp)
        write_stub_lora(lora_dir / _NSGIRL_LORA, _H3_STUB_HEADER)
        write_stub_lora(lora_dir / _FLUX_LORA, _FLUX_STUB_HEADER)

        cases = (
            ("valid_workflow", fixture_valid_workflow(), True),
            ("invalid_workflow", fixture_invalid_workflow(), False),
            ("camera_zoom_conflict", fixture_camera_conflict_workflow(), True),
        )

        passed = 0
        print("h3_preflight selftest", file=out)
        for name, workflow, expect_ok in cases:
            result = run_preflight(workflow, lora_dirs=[lora_dir], source_ar=source_ar)
            verdict = "OK" if result.ok else "FAILED"
            matched = result.ok == expect_ok
            passed += int(matched)
            print(
                f"  {name:<22} -> {verdict} "
                f"(errors={len(result.errors)}, warnings={len(result.warnings)}) "
                f"[{'as expected' if matched else 'UNEXPECTED'}]",
                file=out,
            )
            for message in result.errors:
                print(f"      ERROR  : {message}", file=out)
            for message in result.warnings:
                print(f"      WARNING: {message}", file=out)

        print(f"selftest: {passed}/{len(cases)} фикстур отработали как ожидалось", file=out)
        return 0 if passed == len(cases) else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _resolve_lora_dirs(raw: str | None) -> list[Path]:
    """Вернуть список доступных каталогов LoRA.

    Без явного --lora-dir берутся все каталоги из ``C.LORA_DIRS`` — на хосте их два,
    и facial-realism LoRA лежит не в том же, что NSGIRL.
    """
    if raw:
        candidate = Path(raw)
        return [candidate] if candidate.is_dir() else []
    return [Path(d) for d in C.LORA_DIRS if Path(d).is_dir()]


def _resolve_source_ar(args: argparse.Namespace) -> float | None:
    """Определить AR исходника из одного из трёх взаимозаменяемых флагов."""
    if args.source_ar is not None:
        if args.source_ar <= 0:
            raise WorkflowLoadError("--source-ar должен быть положительным")
        return float(args.source_ar)
    if args.source_size:
        width, height = parse_source_size(args.source_size)
        return C.aspect_ratio(width, height)
    if args.source_image:
        width, height = read_image_size(args.source_image)
        return C.aspect_ratio(width, height)
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="h3_preflight.py",
        description="Проверка ComfyUI-workflow MiniMax H3 I2V до отправки в очередь рендера.",
    )
    parser.add_argument("--workflow", help="Путь к workflow в API-формате (JSON).")
    parser.add_argument("--from-script", help="Python-скрипт, в который зашит workflow.")
    parser.add_argument(
        "--lora-dir",
        help=(
            "Каталог LoRA для проверки файлов. По умолчанию — все доступные "
            f"каталоги из LORA_DIRS: {', '.join(C.LORA_DIRS)}"
        ),
    )
    parser.add_argument("--source-image", help="Исходник I2V — AR берётся из его PNG/JPEG-заголовка.")
    parser.add_argument("--source-size", help="Размер исходника в формате WxH, например 896x1280.")
    parser.add_argument("--source-ar", type=float, help="AR исходника напрямую (width/height).")
    parser.add_argument("--json", action="store_true", help="Вывести отчёт в JSON.")
    parser.add_argument("--selftest", action="store_true", help="Прогнать встроенные фикстуры.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.selftest:
        return run_selftest()

    if not args.workflow and not args.from_script:
        print(
            "error: укажите --workflow <file.json>, --from-script <file.py> или --selftest",
            file=sys.stderr,
        )
        return 2

    if args.workflow and args.from_script:
        print("error: --workflow и --from-script взаимоисключающи", file=sys.stderr)
        return 2

    try:
        source = args.workflow or args.from_script
        workflow = (
            load_workflow_file(args.workflow)
            if args.workflow
            else extract_workflow_from_script(args.from_script)
        )
        source_ar = _resolve_source_ar(args)
    except WorkflowLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    lora_dirs = _resolve_lora_dirs(args.lora_dir)
    if not lora_dirs and args.lora_dir:
        print(f"warning: каталог LoRA не найден: {args.lora_dir}", file=sys.stderr)

    result = run_preflight(workflow, lora_dirs=lora_dirs, source_ar=source_ar)

    if args.json:
        print(
            json.dumps(
                {
                    "workflow": str(source),
                    "ok": result.ok,
                    "errors": result.errors,
                    "warnings": result.warnings,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(render_report(result, label=str(source)))

    return result.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
