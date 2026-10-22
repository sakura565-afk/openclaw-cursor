"""Проверка фактов в диффе, PR и промптах задач.

Зачем этот пакет: агенты Cursor **правдоподобно** выдумывают имена файлов,
числа и каталоги, если в промпте есть пробел. В PR #274 (H3 Knowledge Base)
найдено шесть выдуманных фактов в одном PR — при зелёных тестах агента.
Тесты проверяют код, а не правильность констант, поэтому нужен отдельный
слой сверки с реальным диском.

Состав:

* :mod:`src.factcheck.paths` — реестр корневых каталогов хоста и правила поиска.
* :mod:`src.factcheck.claims` — разбор текста на кандидаты в факты.
* :mod:`src.factcheck.verify` — проверка каждого кандидата против диска.
* :mod:`src.factcheck.report` — вывод отчёта (JSON и человекочитаемый).

Только stdlib: пакет должен импортироваться в окружении агента без
ML-зависимостей.
"""

from __future__ import annotations

from src.factcheck.claims import Claim, ClaimKind, extract_claims, extract_from_diff
from src.factcheck.paths import HostPaths, default_paths
from src.factcheck.report import render_text, to_json
from src.factcheck.verify import Verdict, verify_claim, verify_claims

__all__ = [
    "Claim",
    "ClaimKind",
    "HostPaths",
    "Verdict",
    "default_paths",
    "extract_claims",
    "extract_from_diff",
    "render_text",
    "to_json",
    "verify_claim",
    "verify_claims",
]
