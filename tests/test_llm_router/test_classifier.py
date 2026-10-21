"""Классификатор: детерминизм правил, локальные эмбеддинги, фолбэк llm."""

from __future__ import annotations

from typing import Any

from src.llm_router.classifier import (
    DEEP_PROTOTYPE,
    TIER_DEEP,
    TIER_FAST,
    Classifier,
    cosine,
    embed,
)
from src.llm_router.config import RouterConfig
from src.llm_router.transport import CallResult, StubTransport, TransportNetworkError

FAST_FIXTURES = (
    "Что такое TTL?",
    "Кто написал «Войну и мир»?",
    "Переведи «cache miss» на русский",
)

DEEP_FIXTURES = (
    "Проанализируй логи и посчитай долю попаданий в кеш, сравни две стратегии вытеснения",
    "Отладь traceback: def handler(): return 1/0 — объясни почему падает",
    "Сравни LRU и LFU и объясни почему для нашего профиля нагрузки лучше второй",
)


class ExplodingTransport:
    """Транспорт, который всегда падает сетевой ошибкой."""

    def __init__(self) -> None:
        self.calls = 0

    def call(self, prompt: str, **kwargs: Any) -> CallResult:
        self.calls += 1
        raise TransportNetworkError("сеть недоступна: Temporary failure in name resolution")


def test_rule_strategy_is_deterministic() -> None:
    classifier = Classifier(RouterConfig(classifier_strategy="rule"))
    prompt = "Проанализируй отчёт и посчитай маржу по категориям"

    first = classifier.classify(prompt)
    second = classifier.classify(prompt)

    assert first.to_dict() == second.to_dict()
    assert first.strategy == "rule"


def test_rule_strategy_separates_fast_and_deep() -> None:
    classifier = Classifier(RouterConfig(classifier_strategy="rule"))

    for prompt in FAST_FIXTURES:
        result = classifier.classify(prompt)
        assert result.tier == TIER_FAST, prompt
        assert 0.0 <= result.confidence <= 1.0

    for prompt in DEEP_FIXTURES:
        result = classifier.classify(prompt)
        assert result.tier == TIER_DEEP, prompt
        assert result.signals["score"] > 0


def test_rule_strategy_respects_task_hint() -> None:
    classifier = Classifier(RouterConfig(classifier_strategy="rule"))

    assert classifier.classify("Что такое TTL?", task_hint="analysis").tier == TIER_DEEP
    assert classifier.classify("Сравни и посчитай", task_hint="lookup").tier == TIER_FAST


def test_embedding_cosine_prefers_matching_prototype() -> None:
    deep_vector = embed(DEEP_PROTOTYPE)
    analytic = embed("проанализируй метрики и сравни варианты, посчитай итоги")
    unrelated = embed("рецепт борща: свёкла, капуста, картошка, сметана")

    assert cosine(analytic, deep_vector) > cosine(unrelated, deep_vector)
    assert cosine(deep_vector, deep_vector) > 0.99
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_embedding_strategy_classifies_and_reports_signals() -> None:
    classifier = Classifier(RouterConfig(classifier_strategy="embedding"))

    deep = classifier.classify(
        "проанализируй логи, сравни стратегии вытеснения и объясни почему LRU выигрывает"
    )
    fast = classifier.classify("что такое TTL, кто такой автор, в каком году")

    assert deep.strategy == "embedding"
    assert deep.tier == TIER_DEEP
    assert deep.signals["deep_similarity"] > deep.signals["fast_similarity"]
    assert fast.tier == TIER_FAST


def test_llm_strategy_falls_back_to_rule_on_network_error() -> None:
    transport = ExplodingTransport()
    classifier = Classifier(RouterConfig(classifier_strategy="llm"), transport=transport)

    result = classifier.classify("Проанализируй и посчитай выручку по месяцам")

    assert transport.calls == 1  # ретрая того же пути нет
    assert result.tier == TIER_DEEP
    assert result.signals["requested_strategy"] == "llm"
    assert "TransportNetworkError" in result.signals["fallback_reason"]


def test_llm_strategy_parses_json_answer() -> None:
    transport = StubTransport(
        '```json\n{"tier": "deep", "confidence": 0.91, "reason": "расчёты"}\n```'
    )
    classifier = Classifier(RouterConfig(classifier_strategy="llm"), transport=transport)

    result = classifier.classify("Короткий вопрос")

    assert result.strategy == "llm"
    assert result.tier == TIER_DEEP
    assert result.confidence == 0.91


def test_llm_strategy_falls_back_on_garbage_answer() -> None:
    classifier = Classifier(
        RouterConfig(classifier_strategy="llm"), transport=StubTransport("не json вообще")
    )

    result = classifier.classify("Что такое TTL?")

    assert result.tier == TIER_FAST
    assert result.signals["fallback_reason"] == "модель вернула не JSON"


def test_cascade_calls_llm_only_when_rule_is_unsure() -> None:
    config = RouterConfig(classifier_strategy="cascade", classifier_min_confidence=0.65)
    transport = StubTransport('{"tier": "deep", "confidence": 0.8}')
    classifier = Classifier(config, transport=transport)

    confident = classifier.classify("Что такое TTL?")
    assert confident.signals["stage"] == "rule"
    assert transport.calls == []

    # Нейтральный запрос без маркеров и кода: правила дают confidence 0.5.
    unsure = classifier.classify(
        "Подготовь сводку по продажам за прошлый квартал в виде короткого текста "
        "для внутренней рассылки коллегам"
    )
    assert unsure.signals["stage"] == "llm"
    assert unsure.tier == TIER_DEEP
    assert len(transport.calls) == 1
    assert unsure.signals["rule_tier"] in {TIER_FAST, TIER_DEEP}


def test_classifier_writes_telemetry(tmp_path: Any) -> None:
    from src.llm_router.telemetry import Telemetry

    telemetry = Telemetry(tmp_path / "t.jsonl")
    classifier = Classifier(
        RouterConfig(classifier_strategy="rule"), telemetry=telemetry
    )

    classifier.classify("Проанализируй данные", task_hint="analysis")
    records = telemetry.records()

    assert len(records) == 1
    assert records[0]["task"] == "classify:analysis"
    assert records[0]["decision"]["tier"] == TIER_DEEP


def test_unknown_strategy_degrades_to_rule() -> None:
    classifier = Classifier(RouterConfig())
    result = classifier.classify("Что такое TTL?", strategy="telepathy")

    assert result.strategy == "rule"
