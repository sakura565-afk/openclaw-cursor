"""Исполнитель вызовов: ретраи, таймауты, параллель и каскад по моделям.

Политика ретраев: повторяем только то, что имеет смысл повторять —
429 и 5xx (плюс сетевые сбои и таймауты). Любой другой 4xx (400, 401, 403,
404, 451) — ошибка запроса, ретрай её не исправит, поэтому попытка ровно одна.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.llm_router.config import RouterConfig
from src.llm_router.telemetry import Telemetry
from src.llm_router.transport import (
    CallResult,
    MiniMaxTransport,
    TransportError,
)


def is_retryable(exc: BaseException) -> bool:
    """Ретраить ли ошибку: 429/5xx и сетевые сбои — да, прочие 4xx — нет."""

    if isinstance(exc, TransportError):
        return bool(getattr(exc, "retryable", False))
    return isinstance(exc, (TimeoutError, ConnectionError))


def status_of(exc: BaseException) -> Optional[int]:
    """HTTP-код из ошибки транспорта, если он известен."""

    status = getattr(exc, "status_code", None)
    return int(status) if isinstance(status, int) else None


@dataclass
class Attempt:
    """Итог одного логического вызова (с учётом внутренних ретраев)."""

    prompt_index: int
    model: str
    ok: bool
    text: str = ""
    error: Optional[str] = None
    status_code: Optional[int] = None
    tries: int = 0
    latency_ms: float = 0.0
    tokens: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CascadeOutcome:
    """Результат каскада по моделям для одного промпта."""

    prompt_index: int
    ok: bool
    text: str = ""
    model: Optional[str] = None
    attempts: List[Attempt] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["attempts"] = [attempt.to_dict() for attempt in self.attempts]
        return payload


class Executor:
    """Обёртка над транспортом: ретраи, параллельные и каскадные вызовы."""

    def __init__(
        self,
        config: Optional[RouterConfig] = None,
        *,
        transport: Any = None,
        telemetry: Optional[Telemetry] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.config = config or RouterConfig()
        self.transport = transport if transport is not None else MiniMaxTransport(self.config)
        self.telemetry = telemetry
        self._sleep = sleep
        self._clock = clock

    # --- одиночный вызов ---

    def run(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        timeout: Optional[float] = None,
        max_tokens: Optional[int] = None,
        system: Optional[str] = None,
        prompt_index: int = 0,
        max_attempts: Optional[int] = None,
        tier: Optional[str] = None,
    ) -> Attempt:
        """Вызвать модель с ретраями. Исключение наружу не выпускает."""

        model_name = model or self.config.default_model
        limit = int(max_attempts or self.config.max_attempts)
        backoff = float(self.config.retry_backoff_sec)
        started = self._clock()
        last_error: Optional[BaseException] = None
        tries = 0

        while tries < limit:
            tries += 1
            try:
                result: CallResult = self.transport.call(
                    prompt,
                    model=model_name,
                    effort=effort or self.config.default_effort,
                    max_tokens=max_tokens,
                    system=system,
                    timeout=timeout or self.config.request_timeout,
                )
            except Exception as exc:  # noqa: BLE001 - любая ошибка едет в Attempt
                last_error = exc
                if not is_retryable(exc) or tries >= limit:
                    break
                self._sleep(backoff)
                backoff *= float(self.config.retry_backoff_factor)
                continue

            latency_ms = (self._clock() - started) * 1000.0
            attempt = Attempt(
                prompt_index=prompt_index,
                model=getattr(result, "model", model_name),
                ok=True,
                text=getattr(result, "text", "") or "",
                tries=tries,
                latency_ms=latency_ms,
                tokens=dict(getattr(result, "tokens", {}) or {}),
            )
            self._emit(attempt, effort, tier)
            return attempt

        latency_ms = (self._clock() - started) * 1000.0
        attempt = Attempt(
            prompt_index=prompt_index,
            model=model_name,
            ok=False,
            error=f"{type(last_error).__name__}: {last_error}" if last_error else "unknown error",
            status_code=status_of(last_error) if last_error else None,
            tries=tries,
            latency_ms=latency_ms,
        )
        self._emit(attempt, effort, tier)
        return attempt

    # --- параллель ---

    def parallel(
        self,
        prompts: Sequence[str],
        model: Optional[str] = None,
        *,
        effort: Optional[str] = None,
        timeout: Optional[float] = None,
        system: Optional[str] = None,
        tier: Optional[str] = None,
    ) -> List[Attempt]:
        """Запустить промпты одновременно (adjudication / dual-вызовы).

        Частичная ошибка не роняет батч: в ответе ровно столько элементов,
        сколько было промптов, порядок сохраняется, упавшие — с ``ok=False``.
        """

        items = list(prompts)
        if not items:
            return []
        workers = max(1, min(int(self.config.parallel_workers), len(items)))
        results: List[Optional[Attempt]] = [None] * len(items)

        def _task(index: int) -> Attempt:
            return self.run(
                items[index],
                model=model,
                effort=effort,
                timeout=timeout,
                system=system,
                prompt_index=index,
                tier=tier,
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for index, attempt in zip(range(len(items)), pool.map(_task, range(len(items)))):
                results[index] = attempt

        return [
            attempt
            if attempt is not None
            else Attempt(index, model or self.config.default_model, False, error="no result")
            for index, attempt in enumerate(results)
        ]

    # --- каскад по моделям ---

    def sequential_cascade(
        self,
        prompts: Sequence[str] | str,
        models: Optional[Sequence[str]] = None,
        *,
        effort: Optional[str] = None,
        timeout: Optional[float] = None,
        system: Optional[str] = None,
        tier: Optional[str] = None,
        quality_check: Optional[Callable[[str], bool]] = None,
    ) -> List[CascadeOutcome]:
        """Для каждого промпта перебрать модели по порядку.

        Переход к следующей модели — при ошибке вызова либо при провале
        ``quality_check`` (по умолчанию: непустой ответ длиннее одного символа).
        Возвращается по одному :class:`CascadeOutcome` на промпт со всеми
        промежуточными попытками.
        """

        items = [prompts] if isinstance(prompts, str) else list(prompts)
        candidates = list(models or self.config.candidate_models) or [self.config.default_model]
        checker = quality_check or (lambda text: bool(text and len(text.strip()) > 1))
        outcomes: List[CascadeOutcome] = []

        for index, prompt in enumerate(items):
            outcome = CascadeOutcome(prompt_index=index, ok=False)
            for model_name in candidates:
                attempt = self.run(
                    prompt,
                    model=model_name,
                    effort=effort,
                    timeout=timeout,
                    system=system,
                    prompt_index=index,
                    tier=tier,
                )
                outcome.attempts.append(attempt)
                if attempt.ok and checker(attempt.text):
                    outcome.ok = True
                    outcome.text = attempt.text
                    outcome.model = attempt.model
                    outcome.error = None
                    break
                outcome.error = attempt.error or "ответ не прошёл проверку качества"
            outcomes.append(outcome)
        return outcomes

    # --- телеметрия ---

    def _emit(self, attempt: Attempt, effort: Optional[str], tier: Optional[str] = None) -> None:
        if self.telemetry is None:
            return
        self.telemetry.log(
            task="execute",
            decision={
                "tier": tier or "execute",
                "strategy": "executor",
                "effort": effort or self.config.default_effort,
            },
            model=attempt.model,
            latency_ms=attempt.latency_ms,
            tokens=attempt.tokens,
            outcome="ok" if attempt.ok else "error",
            error=attempt.error,
            extra={"tries": attempt.tries, "status_code": attempt.status_code},
        )
