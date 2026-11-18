"""Проверка кандидатов в факты против реального диска.

Вердикты:

* ``ok`` — подтверждено (файл есть, параметр в реестре).
* ``not_found`` — опровергнуто: файла/каталога нет, секрет зашит в код.
* ``unverifiable`` — проверить нельзя (нет корней на этой машине, число не
  заведено в реестр). Это **не** «подтверждено» — в отчёте такие случаи
  видны отдельно.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Sequence

from src.factcheck.claims import Claim, ClaimKind, SECRET_PATTERNS
from src.factcheck.paths import HostPaths, VERIFIED_NUMBERS, default_paths

#: Секреты, которые в коде считаются ожидаемыми (тестовые значения и т.п.).
#: Каждая запись должна быть обоснована.
EXPECTED_MISSING: dict[str, str] = {
    "example.safetensors": "документационный пример, файла нет и не должно быть",
}

#: Маркеры в контексте строки, по которым понятно, что файл **создаётся**
#: задачей, а не должен уже существовать. Без этого правила любая задача
#: выглядит как «факт опровергнут», потому что её собственный результат
#: ещё не на диске.
_CREATED_BY_TASK_MARKERS: tuple[str, ...] = (
    "создай",
    "создать",
    "создаётся",
    "создается",
    "новый файл",
    "новый модуль",
    "implement",
    "implementa",
    "создайте",
    "добавить файл",
    "создать модуль",
    "создать пакет",
    "создать скрипт",
    "должен быть создан",
    "появится",
    "получится",
)


def _looks_created_by_task(context: str) -> bool:
    """Создаётся ли файл самой задачей — тогда его отсутствие это норма."""
    lowered = context.casefold()
    return any(marker in lowered for marker in _CREATED_BY_TASK_MARKERS)


class Verdict(str, Enum):
    """Итог проверки одного утверждения."""

    OK = "ok"
    NOT_FOUND = "not_found"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class CheckResult:
    """Результат проверки одного утверждения."""

    claim: Claim
    verdict: Verdict
    evidence: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.claim.kind.value,
            "value": self.claim.value,
            "line": self.claim.line,
            "verdict": self.verdict.value,
            "evidence": self.evidence,
            "suggestion": self.suggestion,
            "context": self.claim.context,
        }


def _secret_name(value: str) -> str:
    for name, pattern in SECRET_PATTERNS:
        if pattern.search(value):
            return name
    return "unknown"


def verify_claim(
    claim: Claim,
    paths: Optional[HostPaths] = None,
    *,
    live: bool = True,
) -> CheckResult:
    """Проверить одно утверждение.

    ``live=False`` — режим без доступа к диску: проверяются только
    секреты и синтаксические ошибки в пути. Используется в тестах и в
    CI, где хоста нет.

    Важно: если корни недоступны (машина без нужных каталогов), проверка
    файлов даёт ``unverifiable``, а **не** ``not_found`` — иначе на CI
    без хоста каждый кандидат объявлялся бы опровергнутым.
    """
    paths = paths or default_paths()

    if claim.kind is ClaimKind.SECRET:
        return CheckResult(
            claim=claim,
            verdict=Verdict.NOT_FOUND,
            evidence=f"секрет ({_secret_name(claim.value)}) зашит в код",
            suggestion="заменить на чтение из окружения или credentials",
        )

    if claim.kind is ClaimKind.FILENAME:
        if claim.value in EXPECTED_MISSING:
            return CheckResult(
                claim=claim,
                verdict=Verdict.OK,
                evidence=f"ожидаемо отсутствует: {EXPECTED_MISSING[claim.value]}",
            )
        if not live:
            return CheckResult(
                claim=claim,
                verdict=Verdict.UNVERIFIABLE,
                evidence="проверка на диске отключена",
            )
        if not paths.existing_roots():
            return CheckResult(
                claim=claim,
                verdict=Verdict.UNVERIFIABLE,
                evidence="на этой машине нет ни одного доступного корня — проверить нельзя",
            )
        found = paths.find_file(claim.value)
        if found is not None:
            return CheckResult(
                claim=claim,
                verdict=Verdict.OK,
                evidence=f"найден: {found}",
            )
        if _looks_created_by_task(claim.context):
            return CheckResult(
                claim=claim,
                verdict=Verdict.OK,
                evidence="файл создаётся самой задачей — отсутствие ожидаемо",
            )
        similar = paths.find_similar(claim.value)
        suggestion = f"похожий файл: {similar}" if similar else ""
        return CheckResult(
            claim=claim,
            verdict=Verdict.NOT_FOUND,
            evidence="файл не найден ни в одном корне",
            suggestion=suggestion,
        )

    if claim.kind is ClaimKind.PATH:
        if not live:
            return CheckResult(
                claim=claim,
                verdict=Verdict.UNVERIFIABLE,
                evidence="проверка на диске отключена",
            )
        native = Path(claim.value.replace("/", "\\") if "\\" in claim.value else claim.value)
        if native.exists():
            return CheckResult(
                claim=claim,
                verdict=Verdict.OK,
                evidence=f"существует: {native}",
            )
        # Может, это путь относительно одного из корней.
        name = native.name
        if name:
            found = paths.find_file(name)
            if found is not None:
                return CheckResult(
                    claim=claim,
                    verdict=Verdict.OK,
                    evidence=f"файл найден в корне: {found}",
                )
        similar = paths.find_similar(name) if name else None
        return CheckResult(
            claim=claim,
            verdict=Verdict.NOT_FOUND,
            evidence="путь не существует",
            suggestion=f"похожий файл: {similar}" if similar else "",
        )

    if claim.kind is ClaimKind.NUMBER:
        key, _, raw = claim.value.partition("=")
        expected = VERIFIED_NUMBERS.get(key)
        if expected is None:
            return CheckResult(
                claim=claim,
                verdict=Verdict.UNVERIFIABLE,
                evidence=f"параметр {key} не заведён в реестр",
            )
        try:
            value = float(raw)
        except ValueError:
            return CheckResult(
                claim=claim,
                verdict=Verdict.NOT_FOUND,
                evidence=f"значение {raw!r} не число",
            )
        if expected is None:
            return CheckResult(
                claim=claim,
                verdict=Verdict.UNVERIFIABLE,
                evidence=f"проверенного значения для {key} в реестре нет",
            )
        if abs(value - float(expected)) < 1e-9:
            return CheckResult(
                claim=claim,
                verdict=Verdict.OK,
                evidence=f"{key}={value} совпадает с проверенным",
            )
        return CheckResult(
            claim=claim,
            verdict=Verdict.NOT_FOUND,
            evidence=f"{key}={value}, проверено {expected}",
            suggestion=f"поставить {key}={expected}",
        )

    return CheckResult(claim=claim, verdict=Verdict.UNVERIFIABLE, evidence="неизвестный тип")


def verify_claims(
    claims: Sequence[Claim],
    paths: Optional[HostPaths] = None,
    *,
    live: bool = True,
) -> list[CheckResult]:
    """Проверить набор утверждений."""
    paths = paths or default_paths()
    return [verify_claim(c, paths, live=live) for c in claims]


def summarize(results: Sequence[CheckResult]) -> dict[str, int]:
    """Сводка по вердиктам."""
    out = {v.value: 0 for v in Verdict}
    for r in results:
        out[r.verdict.value] += 1
    return out
