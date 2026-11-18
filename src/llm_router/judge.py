"""Судья ответа: оценка уверенности и детект «не знаю» / «предположу».

Две категории маркеров:

* ``refusal`` — модель прямо говорит, что не знает («I don't know», «не знаю»);
* ``hedge``   — модель угадывает («I assume», «предполагаю», «likely»,
  «probably», «typically»).

Первая всегда требует эскалации, вторая снижает confidence.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: Явный отказ отвечать.
REFUSAL_MARKERS: Tuple[str, ...] = (
    "i don't know",
    "i do not know",
    "i'm not sure",
    "i am not sure",
    "no idea",
    "cannot determine",
    "can't determine",
    "unable to answer",
    "не знаю",
    "не уверен",
    "не могу определить",
    "нет данных",
    "затрудняюсь ответить",
)

#: Догадки и обобщения.
HEDGE_MARKERS: Tuple[str, ...] = (
    "i assume",
    "i'd assume",
    "assuming",
    "likely",
    "probably",
    "typically",
    "usually",
    "perhaps",
    "maybe",
    "might be",
    "предполагаю",
    "предположу",
    "скорее всего",
    "вероятно",
    "обычно",
    "как правило",
    "возможно",
)

_SENTENCE_RE = re.compile(r"[.!?]+\s")
_NUMBER_RE = re.compile(r"\d")


@dataclass
class Judgement:
    """Оценка ответа."""

    confidence: float
    unknown: bool
    hedged: bool
    markers: List[str] = field(default_factory=list)
    reason: str = ""
    signals: Dict[str, Any] = field(default_factory=dict)

    @property
    def uncertain(self) -> bool:
        """Любая неуверенность: отказ или догадка."""

        return self.unknown or self.hedged

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["uncertain"] = self.uncertain
        return payload


def find_markers(text: str, markers: Iterable[str]) -> List[str]:
    """Какие маркеры встретились в тексте (регистр не важен)."""

    lowered = (text or "").lower()
    return [marker for marker in markers if marker in lowered]


def judge(
    response: str,
    task: str = "general",
    criteria: Optional[Sequence[str]] = None,
) -> Judgement:
    """Оценить ответ: confidence в [0, 1] + флаги неуверенности.

    ``criteria`` — список обязательных подстрок/понятий; каждое непокрытое
    требование снижает оценку.
    """

    text = (response or "").strip()
    refusals = find_markers(text, REFUSAL_MARKERS)
    hedges = find_markers(text, HEDGE_MARKERS)
    words = text.split()
    sentences = [part for part in _SENTENCE_RE.split(text) if part.strip()]

    criteria_list = [str(item).strip() for item in (criteria or []) if str(item).strip()]
    covered = [item for item in criteria_list if item.lower() in text.lower()]
    coverage = (len(covered) / len(criteria_list)) if criteria_list else None

    signals: Dict[str, Any] = {
        "task": task,
        "length": len(text),
        "word_count": len(words),
        "sentences": len(sentences),
        "refusal_markers": refusals,
        "hedge_markers": hedges,
        "has_numbers": bool(_NUMBER_RE.search(text)),
        "criteria_total": len(criteria_list),
        "criteria_covered": covered,
        "criteria_coverage": None if coverage is None else round(coverage, 4),
    }
    reasons: List[str] = []

    if not text:
        return Judgement(
            confidence=0.0,
            unknown=True,
            hedged=False,
            markers=[],
            reason="пустой ответ",
            signals=signals,
        )

    confidence = 0.8
    if refusals:
        confidence -= 0.5
        reasons.append("явный отказ: " + ", ".join(refusals[:2]))
    if hedges:
        confidence -= min(0.1 * len(hedges), 0.3)
        reasons.append("догадки: " + ", ".join(hedges[:3]))
    if len(words) < 4:
        confidence -= 0.2
        reasons.append("ответ слишком короткий")
    elif len(words) > 40:
        confidence += 0.05
        reasons.append("развёрнутый ответ")
    if signals["has_numbers"]:
        confidence += 0.05
        reasons.append("есть конкретные числа")
    if coverage is not None:
        confidence += 0.15 * coverage - 0.15 * (1.0 - coverage)
        reasons.append(f"покрытие критериев {coverage:.0%}")

    confidence = round(min(max(confidence, 0.0), 1.0), 4)
    reason = "; ".join(reasons) if reasons else "ответ без признаков неуверенности"
    return Judgement(
        confidence=confidence,
        unknown=bool(refusals),
        hedged=bool(hedges),
        markers=refusals + hedges,
        reason=reason,
        signals=signals,
    )


def needs_escalation(judgement: Judgement, threshold: float = 0.6) -> bool:
    """Поднимать ли задачу на ``deep``.

    Эскалируем, если модель прямо отказалась отвечать либо если confidence
    ниже порога.
    """

    if judgement.unknown:
        return True
    return judgement.confidence < float(threshold)
