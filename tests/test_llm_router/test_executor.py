"""Executor: политика ретраев, параллель и каскад по моделям."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.llm_router.config import RouterConfig
from src.llm_router.executor import Executor, is_retryable
from src.llm_router.transport import (
    CallResult,
    TransportHTTPError,
    TransportNetworkError,
)


class ScriptedTransport:
    """Транспорт по сценарию: ответ — строка, ошибка — исключение."""

    def __init__(self, script: Any) -> None:
        self.script = script
        self.calls: List[Dict[str, Any]] = []

    def call(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        max_tokens: Optional[int] = None,
        system: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> CallResult:
        self.calls.append({"prompt": prompt, "model": model, "effort": effort})
        item = self.script(prompt, model) if callable(self.script) else self.script
        if isinstance(item, BaseException):
            raise item
        return CallResult(text=str(item), model=model or "stub", tokens={"output_tokens": 3})


def make_executor(transport: Any, **config_kwargs: Any) -> Executor:
    config = RouterConfig(**config_kwargs)
    # sleep подменён: тесты не должны ждать backoff.
    return Executor(config, transport=transport, sleep=lambda _seconds: None)


def test_retryable_classification() -> None:
    assert is_retryable(TransportHTTPError(429)) is True
    assert is_retryable(TransportHTTPError(503)) is True
    assert is_retryable(TransportNetworkError("нет сети")) is True
    assert is_retryable(TransportHTTPError(400)) is False
    assert is_retryable(TransportHTTPError(451)) is False
    assert is_retryable(ValueError("прочее")) is False


def test_retry_on_429_makes_exactly_two_attempts() -> None:
    transport = ScriptedTransport(TransportHTTPError(429, "rate limited"))
    executor = make_executor(transport, max_attempts=2, retry_backoff_sec=0.0)

    attempt = executor.run("промпт")

    assert len(transport.calls) == 2
    assert attempt.tries == 2
    assert attempt.ok is False
    assert attempt.status_code == 429


def test_no_retry_on_400() -> None:
    transport = ScriptedTransport(TransportHTTPError(400, "bad request"))
    executor = make_executor(transport, max_attempts=2, retry_backoff_sec=0.0)

    attempt = executor.run("промпт")

    assert len(transport.calls) == 1
    assert attempt.tries == 1
    assert attempt.ok is False
    assert attempt.status_code == 400


def test_retry_succeeds_on_second_attempt() -> None:
    sequence: List[Any] = [TransportHTTPError(503, "upstream"), "готовый ответ"]

    def script(prompt: str, model: Optional[str]) -> Any:
        return sequence.pop(0)

    transport = ScriptedTransport(script)
    executor = make_executor(transport, max_attempts=2, retry_backoff_sec=0.0)

    attempt = executor.run("промпт")

    assert attempt.ok is True
    assert attempt.tries == 2
    assert attempt.text == "готовый ответ"


def test_parallel_survives_partial_failure() -> None:
    def script(prompt: str, model: Optional[str]) -> Any:
        if "boom" in prompt:
            return TransportHTTPError(400, "boom")
        return f"ответ на {prompt}"

    transport = ScriptedTransport(script)
    executor = make_executor(transport, max_attempts=1, parallel_workers=3)

    results = executor.parallel(["первый", "boom", "третий"])

    assert len(results) == 3
    assert [item.prompt_index for item in results] == [0, 1, 2]
    assert [item.ok for item in results] == [True, False, True]
    assert results[1].status_code == 400
    assert results[0].text == "ответ на первый"
    assert results[2].text == "ответ на третий"


def test_parallel_with_empty_input() -> None:
    executor = make_executor(ScriptedTransport("ответ"))
    assert executor.parallel([]) == []


def test_sequential_cascade_moves_to_next_model() -> None:
    def script(prompt: str, model: Optional[str]) -> Any:
        if model == "broken-model":
            return TransportHTTPError(500, "dead")
        if model == "empty-model":
            return ""
        return "хороший ответ"

    transport = ScriptedTransport(script)
    executor = make_executor(transport, max_attempts=1)

    outcomes = executor.sequential_cascade(
        "посчитай метрики", ["broken-model", "empty-model", "good-model"]
    )

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.ok is True
    assert outcome.model == "good-model"
    assert [item.model for item in outcome.attempts] == [
        "broken-model",
        "empty-model",
        "good-model",
    ]


def test_sequential_cascade_reports_total_failure() -> None:
    transport = ScriptedTransport(TransportHTTPError(503, "dead"))
    executor = make_executor(transport, max_attempts=1)

    outcomes = executor.sequential_cascade(["промпт"], ["a", "b"])

    assert outcomes[0].ok is False
    assert outcomes[0].error is not None
    assert len(outcomes[0].attempts) == 2


def test_executor_writes_telemetry(tmp_path: Any) -> None:
    from src.llm_router.telemetry import Telemetry

    telemetry = Telemetry(tmp_path / "t.jsonl")
    executor = Executor(
        RouterConfig(max_attempts=1),
        transport=ScriptedTransport("ответ"),
        telemetry=telemetry,
        sleep=lambda _seconds: None,
    )

    executor.run("промпт", tier="fast")
    records = telemetry.records()

    assert records[0]["task"] == "execute"
    assert records[0]["decision"]["tier"] == "fast"
    assert records[0]["outcome"] == "ok"
