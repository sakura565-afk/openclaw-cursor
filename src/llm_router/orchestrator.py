"""Фасад оркестратора: classifier -> cache -> executor -> judge -> telemetry.

Поток одного запроса:

1. классификатор выбирает tier (``fast``/``deep``);
2. считается ключ кеша (промпт + модель + effort + хеш картинки) и делается
   lookup — попадание завершает запрос;
3. executor вызывает модель выбранного tier (vision-запросы уходят в
   :mod:`src.llm_router.vision`);
4. judge оценивает ответ; если нужна эскалация — повтор на ``deep``;
5. результат и все попытки пишутся в телеметрию.

Запуск локального сценария без сети::

    python -m src.llm_router.orchestrator --selftest
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Union

from src.llm_router.cache import LRUTTLCache, hash_image, make_cache_key
from src.llm_router.classifier import TIER_DEEP, TIER_FAST, Classification, Classifier
from src.llm_router.config import RouterConfig, load_config
from src.llm_router.executor import Attempt, Executor
from src.llm_router.judge import Judgement, judge, needs_escalation
from src.llm_router.telemetry import Telemetry, TelemetryRecord
from src.llm_router.transport import MiniMaxTransport, StubTransport
from src.llm_router.vision import VisionError, analyze_image


@dataclass
class OrchestrationResult:
    """Что получил вызывающий код."""

    text: str
    model: Optional[str]
    tier: str
    confidence: float
    cache_hit: bool
    latency_ms: float
    escalated: bool
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    outcome: str = "ok"
    error: Optional[str] = None
    classification: Optional[Dict[str, Any]] = None
    judgement: Optional[Dict[str, Any]] = None

    @property
    def ok(self) -> bool:
        return self.outcome != "error"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Orchestrator:
    """Сборка всех слоёв. Все зависимости подменяемы — для тестов и selftest."""

    def __init__(
        self,
        config: Optional[RouterConfig] = None,
        *,
        transport: Any = None,
        cache: Optional[LRUTTLCache] = None,
        classifier: Optional[Classifier] = None,
        executor: Optional[Executor] = None,
        telemetry: Optional[Telemetry] = None,
    ) -> None:
        self.config = config or load_config()
        self.transport = transport if transport is not None else MiniMaxTransport(self.config)
        self.telemetry = telemetry if telemetry is not None else self._default_telemetry()
        self.cache = cache if cache is not None else self._default_cache()
        self.classifier = classifier or Classifier(
            self.config, transport=self.transport, telemetry=self.telemetry
        )
        self.executor = executor or Executor(
            self.config, transport=self.transport, telemetry=self.telemetry
        )

    def _default_telemetry(self) -> Telemetry:
        return Telemetry(
            self.config.telemetry_file,
            max_bytes=self.config.telemetry_max_bytes,
            archive_dir=self.config.telemetry_archive,
            enabled=self.config.telemetry_enabled,
        )

    def _default_cache(self) -> LRUTTLCache:
        return LRUTTLCache(
            max_size=self.config.cache_size,
            ttl_sec=self.config.cache_ttl_sec,
            path=self.config.cache_file,
            persist=self.config.cache_persist,
        )

    # --- основной поток ---

    def orchestrate(
        self,
        prompt: str,
        task: str = "general",
        image: Union[bytes, bytearray, None] = None,
        criteria: Optional[Sequence[str]] = None,
    ) -> OrchestrationResult:
        """Провести запрос через все четыре слоя."""

        started = time.perf_counter()
        attempts: List[Dict[str, Any]] = []
        classification = self.classifier.classify(prompt, task_hint=task)
        tier = classification.tier
        image_hash = hash_image(image)

        if image is not None:
            return self._orchestrate_vision(
                prompt, task, bytes(image), image_hash, classification, criteria, started
            )

        model = self.config.model_for_tier(tier)
        effort = self.config.effort_for_tier(tier)
        key = make_cache_key(prompt, model, effort, image_hash=image_hash)

        cached = self.cache.get(key)
        if cached is not None:
            latency_ms = (time.perf_counter() - started) * 1000.0
            result = OrchestrationResult(
                text=str(cached.get("text", "")),
                model=cached.get("model"),
                tier=str(cached.get("tier", tier)),
                confidence=float(cached.get("confidence", 0.0)),
                cache_hit=True,
                latency_ms=latency_ms,
                escalated=bool(cached.get("escalated", False)),
                attempts=[],
                classification=classification.to_dict(),
                judgement=cached.get("judgement"),
            )
            self._emit(task, classification, result)
            return result

        attempt = self.executor.run(
            prompt, model=model, effort=effort, timeout=self.config.request_timeout, tier=tier
        )
        attempts.append(attempt.to_dict())
        escalated = False
        judgement: Optional[Judgement] = None

        if attempt.ok:
            judgement = judge(attempt.text, task=task, criteria=criteria)
            if tier == TIER_FAST and needs_escalation(judgement, self.config.escalation_threshold):
                escalated = True
                deep_attempt = self.executor.run(
                    prompt,
                    model=self.config.model_for_tier(TIER_DEEP),
                    effort=self.config.effort_for_tier(TIER_DEEP),
                    timeout=self.config.request_timeout,
                    tier=TIER_DEEP,
                )
                attempts.append(deep_attempt.to_dict())
                if deep_attempt.ok:
                    deep_judgement = judge(deep_attempt.text, task=task, criteria=criteria)
                    # Оставляем эскалированный ответ только если он не хуже исходного.
                    if deep_judgement.confidence >= judgement.confidence:
                        attempt, judgement, tier = deep_attempt, deep_judgement, TIER_DEEP
                    else:
                        tier = TIER_FAST
                else:
                    tier = TIER_FAST
        elif tier == TIER_FAST:
            # Падение fast-модели — тоже повод попробовать deep.
            deep_attempt = self.executor.run(
                prompt,
                model=self.config.model_for_tier(TIER_DEEP),
                effort=self.config.effort_for_tier(TIER_DEEP),
                timeout=self.config.request_timeout,
                tier=TIER_DEEP,
            )
            attempts.append(deep_attempt.to_dict())
            escalated = True
            if deep_attempt.ok:
                attempt = deep_attempt
                judgement = judge(deep_attempt.text, task=task, criteria=criteria)
                tier = TIER_DEEP

        latency_ms = (time.perf_counter() - started) * 1000.0
        if not attempt.ok:
            result = OrchestrationResult(
                text="",
                model=attempt.model,
                tier=tier,
                confidence=0.0,
                cache_hit=False,
                latency_ms=latency_ms,
                escalated=escalated,
                attempts=attempts,
                outcome="error",
                error=attempt.error,
                classification=classification.to_dict(),
            )
            self._emit(task, classification, result, tokens=attempt.tokens)
            return result

        confidence = judgement.confidence if judgement else 0.0
        result = OrchestrationResult(
            text=attempt.text,
            model=attempt.model,
            tier=tier,
            confidence=confidence,
            cache_hit=False,
            latency_ms=latency_ms,
            escalated=escalated,
            attempts=attempts,
            outcome="escalated" if escalated else "ok",
            classification=classification.to_dict(),
            judgement=judgement.to_dict() if judgement else None,
        )
        self.cache.set(
            key,
            {
                "text": result.text,
                "model": result.model,
                "tier": result.tier,
                "confidence": result.confidence,
                "escalated": result.escalated,
                "judgement": result.judgement,
            },
        )
        self._emit(task, classification, result, tokens=attempt.tokens)
        return result

    # --- vision-ветка ---

    def _orchestrate_vision(
        self,
        prompt: str,
        task: str,
        image: bytes,
        image_hash: str,
        classification: Classification,
        criteria: Optional[Sequence[str]],
        started: float,
    ) -> OrchestrationResult:
        """Картинка идёт в vision-модель; ключ кеша включает её хеш."""

        model = self.config.vision_model
        key = make_cache_key(prompt, model, "vision", image_hash=image_hash)
        cached = self.cache.get(key)
        if cached is not None:
            latency_ms = (time.perf_counter() - started) * 1000.0
            result = OrchestrationResult(
                text=str(cached.get("text", "")),
                model=model,
                tier="vision",
                confidence=float(cached.get("confidence", 0.0)),
                cache_hit=True,
                latency_ms=latency_ms,
                escalated=False,
                attempts=[],
                classification=classification.to_dict(),
            )
            self._emit(task, classification, result)
            return result

        try:
            text = analyze_image(image, prompt, self.config, transport=self.transport)
        except VisionError as exc:
            latency_ms = (time.perf_counter() - started) * 1000.0
            result = OrchestrationResult(
                text="",
                model=model,
                tier="vision",
                confidence=0.0,
                cache_hit=False,
                latency_ms=latency_ms,
                escalated=False,
                attempts=[exc.to_dict()],
                outcome="error",
                error=str(exc),
                classification=classification.to_dict(),
            )
            self._emit(task, classification, result)
            return result

        judgement = judge(text, task=task, criteria=criteria)
        latency_ms = (time.perf_counter() - started) * 1000.0
        result = OrchestrationResult(
            text=text,
            model=model,
            tier="vision",
            confidence=judgement.confidence,
            cache_hit=False,
            latency_ms=latency_ms,
            escalated=False,
            attempts=[{"model": model, "ok": True, "tries": 1}],
            classification=classification.to_dict(),
            judgement=judgement.to_dict(),
        )
        self.cache.set(
            key,
            {
                "text": text,
                "model": model,
                "confidence": judgement.confidence,
                "judgement": result.judgement,
            },
        )
        self._emit(task, classification, result)
        return result

    # --- телеметрия ---

    def _emit(
        self,
        task: str,
        classification: Classification,
        result: OrchestrationResult,
        tokens: Optional[Dict[str, int]] = None,
    ) -> None:
        if self.telemetry is None:
            return
        self.telemetry.record(
            TelemetryRecord(
                task=task,
                decision={
                    "tier": result.tier,
                    "strategy": classification.strategy,
                    "classifier_confidence": classification.confidence,
                },
                model=result.model,
                latency_ms=result.latency_ms,
                cache_hit=result.cache_hit,
                tokens=dict(tokens or {}),
                confidence=result.confidence,
                outcome=result.outcome,
                error=result.error,
                extra={"escalated": result.escalated, "attempts": len(result.attempts)},
            )
        )

    def cache_stats(self) -> Dict[str, int]:
        return self.cache.stats()


def orchestrate(
    prompt: str,
    task: str = "general",
    image: Union[bytes, bytearray, None] = None,
    criteria: Optional[Sequence[str]] = None,
    *,
    config: Optional[RouterConfig] = None,
    transport: Any = None,
) -> OrchestrationResult:
    """Одноразовый вызов оркестратора с настройками по умолчанию."""

    return Orchestrator(config=config, transport=transport).orchestrate(
        prompt, task=task, image=image, criteria=criteria
    )


# --- selftest ---

#: Локальные фикстуры: промпт -> ожидаемый tier.
SELFTEST_FIXTURES = (
    ("Что такое TTL в кеше?", TIER_FAST),
    (
        "Проанализируй логи и посчитай, какая доля запросов попала в кеш; "
        "сравни две стратегии вытеснения и объясни почему одна лучше.",
        TIER_DEEP,
    ),
)

SELFTEST_RESPONSES = {
    "Что такое TTL": "TTL — время жизни записи в кеше, после него запись считается устаревшей.",
    "Проанализируй логи": (
        "Hit rate составил 42%. LRU выигрывает у FIFO на 11 п.п., потому что "
        "горячие ключи переживают вытеснение: 7 из 10 обращений приходятся на 20% ключей."
    ),
    "Сколько будет 2+2": "Не знаю, предполагаю что около четырёх.",
}


def selftest(config: Optional[RouterConfig] = None, *, verbose: bool = True) -> int:
    """Локальный сценарий на фикстурах, без сети. 0 — успех."""

    config = config or load_config()
    transport = StubTransport(SELFTEST_RESPONSES, config=config, default="stub ответ модели")
    orchestrator = Orchestrator(config=config, transport=transport)
    failures: List[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)
        if verbose:
            print(f"  [{'ok' if condition else 'FAIL'}] {message}")

    if verbose:
        print("llm_router selftest (оффлайн, StubTransport)")

    for prompt, expected_tier in SELFTEST_FIXTURES:
        result = orchestrator.orchestrate(prompt, task="general")
        check(result.ok, f"ответ получен для {prompt[:32]!r}")
        check(
            result.classification is not None
            and result.classification["tier"] == expected_tier,
            f"tier={expected_tier} для {prompt[:32]!r}",
        )
        check(result.cache_hit is False, "первый вызов — промах кеша")
        check(bool(result.text), "текст непустой")

        repeated = orchestrator.orchestrate(prompt, task="general")
        check(repeated.cache_hit is True, "повторный вызов пришёл из кеша")
        check(repeated.text == result.text, "кеш вернул тот же текст")

    hedged = orchestrator.orchestrate("Сколько будет 2+2 и почему?", task="general")
    check(hedged.escalated or hedged.confidence < 1.0, "неуверенный ответ отмечен судьёй")

    image_result = orchestrator.orchestrate(
        "Что на картинке?", task="vision", image=b"\x89PNG\r\n\x1a\nfixture"
    )
    check(image_result.tier == "vision", "vision-ветка выбрана для картинки")
    check(image_result.ok, "vision-ответ получен через прямой вызов")

    stats = orchestrator.cache_stats()
    check(stats["hits"] >= len(SELFTEST_FIXTURES), "кеш зафиксировал попадания")

    summary = orchestrator.telemetry.summary()
    check(summary["total"] > 0, "телеметрия пишется")

    if verbose:
        print("cache stats: " + json.dumps(stats, ensure_ascii=False))
        print("telemetry summary: " + json.dumps(summary, ensure_ascii=False, indent=2))
        print("telemetry file: " + str(orchestrator.telemetry.path))

    if failures:
        if verbose:
            print("FAILED:")
            for message in failures:
                print(f"  - {message}")
        return 1
    if verbose:
        print("OK")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="4-слойный LLM-оркестратор")
    parser.add_argument("--selftest", action="store_true", help="оффлайн-прогон на фикстурах")
    parser.add_argument("--prompt", help="выполнить один реальный запрос (нужен токен)")
    parser.add_argument("--task", default="general", help="метка задачи для телеметрии")
    parser.add_argument("--summary", action="store_true", help="напечатать агрегаты телеметрии")
    parser.add_argument("--config", help="путь к JSON-конфигу роутера")
    args = parser.parse_args(argv)

    config = load_config(args.config) if args.config else load_config()

    if args.selftest:
        return selftest(config)

    if args.summary:
        telemetry = Telemetry(
            config.telemetry_file,
            max_bytes=config.telemetry_max_bytes,
            archive_dir=config.telemetry_archive,
        )
        print(json.dumps(telemetry.summary(include_archive=True), ensure_ascii=False, indent=2))
        return 0

    if args.prompt:
        result = Orchestrator(config=config).orchestrate(args.prompt, task=args.task)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.ok else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
