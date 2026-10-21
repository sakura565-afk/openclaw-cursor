"""Классификатор сложности запроса: выбор tier ``fast`` / ``deep``.

Четыре стратегии, переключаемые конфигом (``classifier_strategy``):

* ``rule``      — детерминированные правила (длина, код, глаголы-маркеры);
* ``embedding`` — локальные hashing-эмбеддинги + косинус к прототипам, без сети;
* ``llm``       — сам MiniMax отвечает JSON-ом, при неудаче — фолбэк на ``rule``;
* ``cascade``   — ``rule``, и только при низкой уверенности — ``llm``.

Любая стратегия обязана вернуть :class:`Classification`, а не исключение:
роутер не должен падать из-за классификатора.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.llm_router.config import RouterConfig
from src.llm_router.telemetry import Telemetry

TIER_FAST = "fast"
TIER_DEEP = "deep"
STRATEGIES = ("rule", "embedding", "llm", "cascade")

#: Маркеры тяжёлых задач: счёт, анализ, сравнение, отладка.
DEEP_MARKERS: Tuple[str, ...] = (
    "посчитай",
    "подсчитай",
    "рассчитай",
    "вычисли",
    "проанализируй",
    "анализ",
    "сравни",
    "сравнение",
    "объясни почему",
    "разбери",
    "отладь",
    "рефактор",
    "оптимизируй",
    "докажи",
    "спроектируй",
    "пошагово",
    "analyze",
    "analyse",
    "compare",
    "calculate",
    "compute",
    "explain why",
    "debug",
    "refactor",
    "optimize",
    "prove",
    "design",
    "step by step",
    "trade-off",
    "root cause",
)

#: Маркеры коротких factual-вопросов.
FAST_MARKERS: Tuple[str, ...] = (
    "что такое",
    "кто такой",
    "кто написал",
    "в каком году",
    "столица",
    "переведи",
    "what is",
    "who is",
    "when was",
    "capital of",
    "define ",
    "translate",
    "spell",
)

#: Признаки кода в промпте.
CODE_MARKERS: Tuple[str, ...] = (
    "```",
    "def ",
    "class ",
    "import ",
    "return ",
    "select ",
    "traceback",
    "=>",
    "();",
    "{}",
    "#!/",
)

DEEP_HINTS = frozenset({"deep", "analysis", "analytics", "code", "reasoning", "adjudication"})
FAST_HINTS = frozenset({"fast", "factual", "lookup", "chat", "simple"})

EMBED_DIM = 512

#: Прототипы для ``embedding``-стратегии. Текст специально «многословный»,
#: чтобы hashing bag-of-words давал устойчивый центроид.
DEEP_PROTOTYPE = (
    "проанализируй данные и сравни варианты, посчитай метрики, объясни почему "
    "выбранное решение лучше, разбери пошагово архитектуру, найди корневую причину "
    "ошибки в коде, отладь трассировку, оптимизируй запрос, оцени компромиссы "
    "analyze compare calculate metrics explain why reasoning step by step debug "
    "root cause refactor optimize trade-off architecture design review"
)
FAST_PROTOTYPE = (
    "что такое, кто такой, в каком году, столица страны, переведи фразу, "
    "короткий факт, дай определение, как называется, да или нет "
    "what is who is when was capital of define translate short fact yes no lookup"
)

_TOKEN_RE = re.compile(r"[\w'-]+", re.UNICODE)

LLM_CLASSIFIER_SYSTEM = (
    "Ты маршрутизатор запросов. Отвечай ТОЛЬКО JSON-объектом вида "
    '{"tier": "fast"|"deep", "confidence": 0.0-1.0, "reason": "..."}. '
    "tier=deep — если нужны вычисления, анализ, работа с кодом или многошаговые "
    "рассуждения; tier=fast — если это короткий факт или простая переформулировка."
)


@dataclass
class Classification:
    """Решение классификатора."""

    strategy: str
    tier: str
    confidence: float
    reason: str
    signals: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def decision(self) -> Dict[str, Any]:
        """Компактное представление для телеметрии."""

        return {"tier": self.tier, "strategy": self.strategy}


# --- локальные эмбеддинги (без сети и внешних моделей) ---


def tokenize(text: str) -> List[str]:
    """Юникодная токенизация в нижнем регистре + биграммы."""

    words = [token for token in _TOKEN_RE.findall((text or "").lower()) if token]
    bigrams = [f"{first}_{second}" for first, second in zip(words, words[1:])]
    return words + bigrams


def embed(text: str, dim: int = EMBED_DIM) -> List[float]:
    """Hashing bag-of-words: токен -> индекс по стабильному хешу."""

    vector = [0.0] * dim
    for token in tokenize(text):
        # blake2b, а не hash(): встроенный hash рандомизирован между процессами.
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        vector[int.from_bytes(digest, "big") % dim] += 1.0
    return vector


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Косинусная близость; нулевой вектор даёт 0.0."""

    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Вытащить первый JSON-объект из ответа модели."""

    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", stripped).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


class Classifier:
    """Фасад над четырьмя стратегиями классификации."""

    def __init__(
        self,
        config: Optional[RouterConfig] = None,
        *,
        transport: Any = None,
        telemetry: Optional[Telemetry] = None,
        clock: Any = time.perf_counter,
    ) -> None:
        self.config = config or RouterConfig()
        self.transport = transport
        self.telemetry = telemetry
        self._clock = clock
        self._deep_vector = embed(DEEP_PROTOTYPE)
        self._fast_vector = embed(FAST_PROTOTYPE)

    # --- публичный API ---

    def classify(
        self,
        prompt: str,
        task_hint: Optional[str] = None,
        *,
        strategy: Optional[str] = None,
    ) -> Classification:
        """Выбрать tier. Исключений не бросает: худший случай — ``rule``."""

        chosen = (strategy or self.config.classifier_strategy or "rule").lower()
        if chosen not in STRATEGIES:
            chosen = "rule"
        started = self._clock()
        try:
            if chosen == "rule":
                result = self.classify_rule(prompt, task_hint)
            elif chosen == "embedding":
                result = self.classify_embedding(prompt, task_hint)
            elif chosen == "llm":
                result = self.classify_llm(prompt, task_hint)
            else:
                result = self.classify_cascade(prompt, task_hint)
        except Exception as exc:  # pragma: no cover - страховка от неизвестных сбоев
            result = self.classify_rule(prompt, task_hint)
            result.signals["fallback_reason"] = f"{type(exc).__name__}: {exc}"
            result.signals["requested_strategy"] = chosen
        latency_ms = (self._clock() - started) * 1000.0
        self._emit(result, latency_ms, task_hint)
        return result

    # --- стратегия rule ---

    def classify_rule(self, prompt: str, task_hint: Optional[str] = None) -> Classification:
        """Детерминированные правила: одинаковый вход — одинаковый выход."""

        text = prompt or ""
        lowered = text.lower()
        words = _TOKEN_RE.findall(lowered)
        deep_hits = [marker for marker in DEEP_MARKERS if marker in lowered]
        fast_hits = [marker for marker in FAST_MARKERS if marker in lowered]
        has_code = any(marker in lowered for marker in CODE_MARKERS)

        signals: Dict[str, Any] = {
            "length": len(text),
            "word_count": len(words),
            "has_code": has_code,
            "deep_markers": deep_hits[:5],
            "fast_markers": fast_hits[:5],
            "task_hint": task_hint,
            "lines": text.count("\n") + 1 if text else 0,
        }

        score = 0.0
        reasons: List[str] = []
        if len(text) > self.config.rule_deep_length:
            score += 2.0
            reasons.append(f"длина {len(text)} > {self.config.rule_deep_length}")
        if len(words) > 80:
            score += 1.0
            reasons.append(f"{len(words)} слов")
        if has_code:
            score += 2.0
            reasons.append("в промпте код")
        if deep_hits:
            score += 1.5 * min(len(deep_hits), 2)
            reasons.append("маркеры анализа: " + ", ".join(deep_hits[:2]))
        if fast_hits:
            score -= 1.5
            reasons.append("factual-маркеры: " + ", ".join(fast_hits[:2]))
        if len(text) < 80 and not deep_hits and not has_code:
            score -= 1.5
            reasons.append("короткий запрос без маркеров анализа")
        signals["score"] = round(score, 3)
        tier = TIER_DEEP if score > 0 else TIER_FAST
        confidence = round(min(0.95, 0.5 + 0.45 * min(abs(score), 4.0) / 4.0), 4)

        # Явный task_hint от вызывающего кода сильнее эвристик по тексту.
        hint = (task_hint or "").lower()
        if hint in DEEP_HINTS or hint in FAST_HINTS:
            tier = TIER_DEEP if hint in DEEP_HINTS else TIER_FAST
            confidence = max(confidence, 0.9)
            signals["hint_override"] = tier
            reasons.append(f"hint={hint} задаёт tier={tier}")
        reason = "; ".join(reasons) if reasons else "нейтральный запрос"
        return Classification("rule", tier, confidence, reason, signals)

    # --- стратегия embedding ---

    def classify_embedding(self, prompt: str, task_hint: Optional[str] = None) -> Classification:
        """Косинус к двум локальным прототипам; сети не требует."""

        vector = embed(prompt or "")
        deep_sim = cosine(vector, self._deep_vector)
        fast_sim = cosine(vector, self._fast_vector)
        signals: Dict[str, Any] = {
            "deep_similarity": round(deep_sim, 6),
            "fast_similarity": round(fast_sim, 6),
            "task_hint": task_hint,
        }
        total = deep_sim + fast_sim
        if total <= 0.0:
            fallback = self.classify_rule(prompt, task_hint)
            fallback.strategy = "embedding"
            fallback.signals.update(signals)
            fallback.signals["fallback_reason"] = "нулевое пересечение с прототипами"
            return fallback

        margin = abs(deep_sim - fast_sim) / total
        tier = TIER_DEEP if deep_sim >= fast_sim else TIER_FAST
        confidence = round(min(0.95, 0.5 + 0.45 * min(margin * 3.0, 1.0)), 4)
        signals["margin"] = round(margin, 6)
        reason = f"cos(deep)={deep_sim:.4f} vs cos(fast)={fast_sim:.4f}"
        return Classification("embedding", tier, confidence, reason, signals)

    # --- стратегия llm ---

    def classify_llm(self, prompt: str, task_hint: Optional[str] = None) -> Classification:
        """Спросить модель. Любая неудача — тихий фолбэк на ``rule``."""

        transport = self.transport
        if transport is None:
            return self._llm_fallback(prompt, task_hint, "транспорт не сконфигурирован")

        query = (
            "Запрос пользователя (может быть усечён):\n"
            f"{(prompt or '')[:1500]}\n\n"
            f"Подсказка по задаче: {task_hint or 'нет'}\n"
            "Верни только JSON."
        )
        try:
            result = transport.call(
                query,
                model=self.config.classifier_model,
                effort="low",
                max_tokens=256,
                system=LLM_CLASSIFIER_SYSTEM,
                timeout=self.config.classifier_timeout,
            )
        except Exception as exc:
            return self._llm_fallback(prompt, task_hint, f"{type(exc).__name__}: {exc}")

        payload = _extract_json(getattr(result, "text", "") or "")
        if not payload:
            return self._llm_fallback(prompt, task_hint, "модель вернула не JSON")
        tier = str(payload.get("tier", "")).strip().lower()
        if tier not in {TIER_FAST, TIER_DEEP}:
            return self._llm_fallback(prompt, task_hint, f"неизвестный tier: {tier!r}")
        try:
            confidence = float(payload.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        confidence = round(min(max(confidence, 0.0), 1.0), 4)
        signals = {
            "model": getattr(result, "model", self.config.classifier_model),
            "tokens": getattr(result, "tokens", {}) or {},
            "task_hint": task_hint,
        }
        reason = str(payload.get("reason") or "решение llm-классификатора")[:300]
        return Classification("llm", tier, confidence, reason, signals)

    def _llm_fallback(
        self, prompt: str, task_hint: Optional[str], why: str
    ) -> Classification:
        """Вернуть результат ``rule``, отметив причину фолбэка в signals."""

        fallback = self.classify_rule(prompt, task_hint)
        fallback.signals["requested_strategy"] = "llm"
        fallback.signals["fallback_reason"] = why
        fallback.reason = f"llm-классификатор недоступен ({why}); правила: {fallback.reason}"
        return fallback

    # --- стратегия cascade ---

    def classify_cascade(self, prompt: str, task_hint: Optional[str] = None) -> Classification:
        """Правила сразу; llm — только если правила не уверены."""

        rule_result = self.classify_rule(prompt, task_hint)
        threshold = self.config.classifier_min_confidence
        if rule_result.confidence >= threshold:
            rule_result.strategy = "cascade"
            rule_result.signals["stage"] = "rule"
            return rule_result

        llm_result = self.classify_llm(prompt, task_hint)
        llm_result.strategy = "cascade"
        llm_result.signals["stage"] = "llm"
        llm_result.signals["rule_confidence"] = rule_result.confidence
        llm_result.signals["rule_tier"] = rule_result.tier
        return llm_result

    # --- телеметрия ---

    def _emit(self, result: Classification, latency_ms: float, task_hint: Optional[str]) -> None:
        if self.telemetry is None:
            return
        self.telemetry.log(
            task=f"classify:{task_hint or 'general'}",
            decision=result.decision,
            model=result.signals.get("model"),
            latency_ms=latency_ms,
            confidence=result.confidence,
            outcome="error" if result.signals.get("fallback_reason") else "ok",
            error=result.signals.get("fallback_reason"),
            extra={"reason": result.reason},
        )
