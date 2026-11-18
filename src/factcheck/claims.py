"""Извлечение кандидатов в факты из текста.

Кандидат — это утверждение, которое можно проверить: путь, имя файла с
известным расширением, числовой параметр или встроенный секрет.

Ключевое правило: из диффа проверяются **только добавленные строки** (с
``+``). Удалённые строки уже были в репозитории и к PR отношения не имеют.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Iterator, Sequence

from src.factcheck.paths import KNOWN_EXTENSIONS, VERIFIED_NUMBERS


class ClaimKind(str, Enum):
    """Тип проверяемого утверждения."""

    PATH = "path"
    FILENAME = "filename"
    NUMBER = "number"
    SECRET = "secret"


@dataclass(frozen=True)
class Claim:
    """Одно проверяемое утверждение."""

    kind: ClaimKind
    value: str
    line: int = 0
    context: str = ""

    def __str__(self) -> str:
        return f"{self.kind.value}:{self.value}"


#: Windows-путь: буква диска, двоеточие, затем сегменты.
_WINDOWS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"[A-Za-z]:[\\/][^\s\"'`<>|]*"
)
#: POSIX-путь, начинающийся с корня.
_POSIX_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])/(?:[A-Za-z0-9_.\-]+/)+[A-Za-z0-9_.\-]+"
)
#: Имя файла с известным расширением.
_FILENAME_RE = re.compile(
    r"(?<![A-Za-z0-9_.\-])"
    r"[A-Za-z0-9_.\-]+(?:" + "|".join(re.escape(e) for e in KNOWN_EXTENSIONS) + r")"
    r"(?![A-Za-z0-9_.\-])"
)
#: Числовой параметр вида ``name=0.45`` или ``name: 0.45``.
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(k) for k in VERIFIED_NUMBERS) + r")"
    r"\s*[:=]\s*(-?\d+(?:\.\d+)?)"
)
#: Встроенные секреты.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cursor_api_key", re.compile(r"crsr_[0-9a-f]{20,}")),
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("github_oauth", re.compile(r"gho_[A-Za-z0-9]{20,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
)


def _is_skippable(line: str) -> bool:
    """Строки, которые нельзя считать фактами."""
    stripped = line.strip()
    if stripped.startswith("#") and "sk-" not in stripped:
        return True
    return False


def _candidates_in_line(line: str) -> list[tuple[ClaimKind, str]]:
    """Кандидаты одной строки, без дублей и пересечений.

    Имя файла, уже покрытое найденным путём, отдельно не выдаётся: иначе
    один и тот же факт учитывался бы дважды.
    """
    found: list[tuple[ClaimKind, str]] = []
    paths: list[str] = []

    for match in _WINDOWS_PATH_RE.finditer(line):
        value = match.group(0).rstrip(".,;:)]}")
        paths.append(value)
        found.append((ClaimKind.PATH, value))
    for match in _POSIX_PATH_RE.finditer(line):
        value = match.group(0)
        if any(value in p for p in paths):
            continue
        paths.append(value)
        found.append((ClaimKind.PATH, value))

    for match in _FILENAME_RE.finditer(line):
        value = match.group(0)
        if any(value in p for p in paths):
            continue
        found.append((ClaimKind.FILENAME, value))

    for match in _NUMBER_RE.finditer(line):
        found.append((ClaimKind.NUMBER, f"{match.group(1)}={match.group(2)}"))

    for _, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(line):
            found.append((ClaimKind.SECRET, match.group(0)))

    return found


def extract_claims(text: str) -> list[Claim]:
    """Извлечь кандидаты в факты из произвольного текста."""
    claims: list[Claim] = []
    seen: set[tuple[ClaimKind, str, int]] = set()

    for number, line in enumerate(text.splitlines(), start=1):
        if _is_skippable(line):
            continue
        for kind, value in _candidates_in_line(line):
            key = (kind, value, number)
            if key in seen:
                continue
            seen.add(key)
            claims.append(
                Claim(kind=kind, value=value, line=number, context=line.strip()[:160])
            )
    return claims


def extract_from_diff(diff: str) -> list[Claim]:
    """Извлечь кандидаты только из добавленных строк диффа.

    Строки с ``-`` (удалённые) и ``+++``/``---`` (заголовки файлов)
    пропускаются: они не являются новыми утверждениями PR.
    """
    text: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            text.append(line[1:])
    return extract_claims("\n".join(text))


def iter_secret_matches(text: str) -> Iterator[tuple[str, str]]:
    """Найти встроенные секреты: (имя паттерна, значение)."""
    for name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            yield name, match.group(0)


def count_claims(claims: Sequence[Claim]) -> dict[str, int]:
    """Сколько кандидатов каждого вида."""
    out: dict[str, int] = {}
    for claim in claims:
        out[claim.kind.value] = out.get(claim.kind.value, 0) + 1
    return out
