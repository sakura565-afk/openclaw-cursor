"""Judge: маркеры неуверенности, confidence и порог эскалации."""

from __future__ import annotations

from src.llm_router.judge import judge, needs_escalation


def test_detects_explicit_unknown_in_both_languages() -> None:
    english = judge("I don't know the exact number for this quarter.")
    russian = judge("Не знаю, таких данных у меня нет.")

    assert english.unknown is True
    assert russian.unknown is True
    assert needs_escalation(english, threshold=0.6) is True
    assert needs_escalation(russian, threshold=0.6) is True
    assert "i don't know" in english.markers
    assert "не знаю" in russian.markers


def test_detects_hedging() -> None:
    hedged = judge("Предполагаю, что обычно это занимает около минуты.")

    assert hedged.hedged is True
    assert hedged.unknown is False
    assert hedged.uncertain is True
    assert hedged.confidence < 0.8
    assert needs_escalation(hedged, threshold=0.75) is True


def test_confident_answer_does_not_escalate() -> None:
    confident = judge(
        "Hit rate за сутки — 42%: 4200 попаданий из 10000 запросов. "
        "LRU вытеснил 318 записей, TTL истёк у 97. Горячие ключи переживают вытеснение, "
        "поэтому доля попаданий выросла на 11 процентных пунктов относительно FIFO."
    )

    assert confident.unknown is False
    assert confident.hedged is False
    assert confident.confidence >= 0.8
    assert needs_escalation(confident, threshold=0.6) is False


def test_empty_answer_is_unknown() -> None:
    empty = judge("   ")

    assert empty.confidence == 0.0
    assert empty.unknown is True
    assert needs_escalation(empty, threshold=0.1) is True


def test_criteria_coverage_moves_confidence() -> None:
    response = "Отчёт включает hit rate и латентность по tier."

    covered = judge(response, criteria=["hit rate", "латентность"])
    missed = judge(response, criteria=["hit rate", "эскалации", "токены"])

    assert covered.signals["criteria_coverage"] == 1.0
    assert covered.confidence > missed.confidence
    assert missed.signals["criteria_covered"] == ["hit rate"]


def test_judgement_serialization_includes_uncertain_flag() -> None:
    payload = judge("Скорее всего, да.").to_dict()

    assert payload["uncertain"] is True
    assert payload["signals"]["hedge_markers"]
