"""Реестр корневых каталогов хоста.

Здесь лежат **проверенные** пути. Агент не должен выдумывать каталоги — если
каталога нет в реестре, проверка честно скажет «проверить нельзя», а не
«не найдено».

Пути проверены на диске 21.10.2026 (см. комментарии). Реестр можно расширять
через :func:`HostPaths.with_roots`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

#: Каталоги с LoRA для ComfyUI. На хосте их **два** — агент в PR #274
#: указал только один, поэтому файл «не существовал».
DEFAULT_LORA_ROOTS: tuple[str, ...] = (
    r"C:\Users\user\comfyui\ComfyUI_windows_portable\ComfyUI\models\loras",
    r"E:\AI\models\comfyui\loras",
)

#: Корень ComfyUI.
DEFAULT_COMFY_ROOTS: tuple[str, ...] = (
    r"C:\Users\user\comfyui\ComfyUI_windows_portable\ComfyUI",
)

#: Интерпретаторы и утилиты. В PATH планировщика первым идёт venv Hermes,
#: поэтому «python» без полного пути уезжает не туда.
DEFAULT_BIN_ROOTS: tuple[str, ...] = (
    r"C:\Users\user\AppData\Local\Programs\Python\Python312",
    r"C:\nvm4w\nodejs",
    r"C:\Program Files\nodejs",
)

#: Корень рабочего пространства OpenClaw.
DEFAULT_WORKSPACE_ROOTS: tuple[str, ...] = (
    r"C:\Users\user\.openclaw\workspace",
    r"C:\Users\user\.openclaw",
)

#: Корень репозитория openclaw-cursor. Файлы из него упоминаются в промптах
#: как «уже существующие модули репозитория» — их надо искать именно там,
#: а не в общих корнях.
DEFAULT_REPO_ROOTS: tuple[str, ...] = (
    r"C:\Users\user\.openclaw\workspace\cursor-repo",
    r"F:\AI\comfyui-qwen",
)

#: Расширения, для которых имя файла — проверяемый факт.
KNOWN_EXTENSIONS: tuple[str, ...] = (
    ".safetensors",
    ".py",
    ".json",
    ".md",
    ".ps1",
    ".cmd",
    ".exe",
    ".yaml",
    ".yml",
    ".toml",
    ".txt",
)

#: Параметры, значения которых агент подставляет из головы.
#: Ключ — имя параметра, значение — проверенное значение (None = не проверяем).
VERIFIED_NUMBERS: dict[str, float | None] = {
    "strength_model": None,
    "strength_clip": None,
    "shift_video": None,
    "shift_audio": None,
    "max_tokens": None,
    "length": None,
    "width": None,
    "height": None,
    "steps": None,
}


@dataclass
class HostPaths:
    """Корневые каталоги, в которых ищутся упомянутые файлы."""

    lora_roots: tuple[str, ...] = DEFAULT_LORA_ROOTS
    comfy_roots: tuple[str, ...] = DEFAULT_COMFY_ROOTS
    bin_roots: tuple[str, ...] = DEFAULT_BIN_ROOTS
    workspace_roots: tuple[str, ...] = DEFAULT_WORKSPACE_ROOTS
    repo_roots: tuple[str, ...] = DEFAULT_REPO_ROOTS
    extra_roots: tuple[str, ...] = field(default_factory=tuple)
    #: Максимальная глубина рекурсивного поиска внутри корня.
    search_depth: int = 3
    #: Кэш индекса имён файлов (заполняется лениво).
    _index_cache: Optional[dict[str, Path]] = field(default=None, repr=False, compare=False)

    def all_roots(self) -> tuple[str, ...]:
        """Все корни, в которых имеет смысл искать файл."""
        seen: list[str] = []
        for group in (
            self.lora_roots,
            self.comfy_roots,
            self.bin_roots,
            self.workspace_roots,
            self.repo_roots,
            self.extra_roots,
        ):
            for item in group:
                if item and item not in seen:
                    seen.append(item)
        return tuple(seen)

    def existing_roots(self) -> tuple[Path, ...]:
        """Только те корни, которые реально есть на этой машине."""
        out: list[Path] = []
        for root in self.all_roots():
            path = Path(root)
            if path.exists():
                out.append(path)
        return tuple(out)

    def with_roots(self, *roots: str) -> "HostPaths":
        """Вернуть копию с дополнительными корнями."""
        return HostPaths(
            lora_roots=self.lora_roots,
            comfy_roots=self.comfy_roots,
            bin_roots=self.bin_roots,
            workspace_roots=self.workspace_roots,
            repo_roots=self.repo_roots,
            extra_roots=self.extra_roots + tuple(roots),
            search_depth=self.search_depth,
        )

    def _walk(self, root: Path):
        """Обойти корень до search_depth, не заходя в .git и кэши."""
        skip = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache"}
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            current = Path(dirpath)
            dirnames[:] = [d for d in dirnames if d not in skip]
            if len(current.parts) - base_depth >= self.search_depth:
                dirnames[:] = []
            for name in filenames:
                yield current / name

    def find_file(self, name: str) -> Optional[Path]:
        """Найти файл по имени в корнях. None, если нигде нет."""
        target = Path(name).name
        for root in self.existing_roots():
            direct = root / target
            if direct.is_file():
                return direct
        return self._name_index().get(target)

    def _name_index(self) -> dict[str, Path]:
        """Индекс «имя файла -> путь» по всем корням.

        Строится один раз и кэшируется: обход диска на каждый кандидат
        слишком дорог (на хосте это десятки секунд на промпт).
        """
        if self._index_cache is not None:
            return self._index_cache
        index: dict[str, Path] = {}
        for root in self.existing_roots():
            for candidate in self._walk(root):
                index.setdefault(candidate.name, candidate)
        self._index_cache = index
        return index

    def find_similar(self, name: str) -> Optional[Path]:
        """Найти файл с похожим именем — чтобы предложить замену.

        Сравнение по общему префиксу/суффиксу: агент чаще всего
        переставляет или переименовывает кусок имени, а не выдумывает его
        целиком.
        """
        target = Path(name).name
        best: Optional[Path] = None
        best_score = 0.0
        for candidate in self._name_index().values():
            score = _similarity(target, candidate.name)
            if score > best_score:
                best_score, best = score, candidate
        if best is not None and best_score >= 0.6:
            return best
        return None


def _similarity(a: str, b: str) -> float:
    """Доля совпадающих символов (быстрый аналог SequenceMatcher)."""
    if not a or not b:
        return 0.0
    common = 0
    pool = list(b)
    for ch in a:
        if ch in pool:
            pool.remove(ch)
            common += 1
    return common / max(len(a), len(b))


def default_paths() -> HostPaths:
    """Реестр по умолчанию, с поправкой на текущую ОС.

    На не-Windows машине абсолютные пути Windows бессмысленны, поэтому
    берутся корни из переменных окружения.
    """
    if os.name == "nt":
        return HostPaths()
    home = Path.home()
    return HostPaths(
        lora_roots=(),
        comfy_roots=(),
        bin_roots=(),
        workspace_roots=(str(home),),
    )


def iter_known_extensions() -> Iterable[str]:
    """Расширения, которые считаем проверяемыми именами файлов."""
    return KNOWN_EXTENSIONS
