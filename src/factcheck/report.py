"""Вывод отчёта по проверке фактов."""

from __future__ import annotations

from typing import Sequence

from src.factcheck.verify import CheckResult, Verdict, summarize

_VERDICT_MARK = {
    Verdict.OK: "OK",
    Verdict.NOT_FOUND: "НЕ НАЙДЕНО",
    Verdict.UNVERIFIABLE: "НЕ ПРОВЕРИТЬ",
}


def to_json(results: Sequence[CheckResult]) -> str:
    """Отчёт в JSON."""
    import json

    payload = {
        "summary": summarize(results),
        "results": [r.to_dict() for r in results],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_text(results: Sequence[CheckResult]) -> str:
    """Человекочитаемый отчёт."""
    counts = summarize(results)
    lines = [
        "Проверка фактов",
        "=" * 60,
        f"Всего: {len(results)}  "
        f"подтверждено: {counts[Verdict.OK.value]}  "
        f"опровергнуто: {counts[Verdict.NOT_FOUND.value]}  "
        f"не проверено: {counts[Verdict.UNVERIFIABLE.value]}",
        "",
    ]
    if not results:
        lines.append("Кандидатов не найдено.")
        return "\n".join(lines)

    current_kind = None
    for result in results:
        kind = result.claim.kind.value
        if kind != current_kind:
            current_kind = kind
            lines.append(f"--- {kind} ---")
        mark = _VERDICT_MARK[result.verdict]
        line = f"  [{mark:12}] {result.claim.value}"
        if result.claim.line:
            line += f"  (строка {result.claim.line})"
        lines.append(line)
        if result.evidence:
            lines.append(f"                 {result.evidence}")
        if result.suggestion:
            lines.append(f"                 -> {result.suggestion}")
    return "\n".join(lines)
