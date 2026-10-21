"""Оркестратор end-to-end на замоканном транспорте: кеш, эскалация, vision."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.llm_router.cache import LRUTTLCache
from src.llm_router.config import RouterConfig
from src.llm_router.executor import Executor
from src.llm_router.orchestrator import Orchestrator, selftest
from src.llm_router.telemetry import Telemetry
from src.llm_router.transport import StubTransport, TransportHTTPError


def make_orchestrator(
    tmp_path: Path,
    responses: Any = "Готовый ответ модели с числом 42 и достаточной длиной текста.",
    **config_kwargs: Any,
) -> Orchestrator:
    config = RouterConfig(
        classifier_strategy=config_kwargs.pop("classifier_strategy", "rule"),
        cache_size=config_kwargs.pop("cache_size", 16),
        cache_ttl_sec=config_kwargs.pop("cache_ttl_sec", 600.0),
        max_attempts=config_kwargs.pop("max_attempts", 1),
        telemetry_path=str(tmp_path / "telemetry.jsonl"),
        telemetry_archive_dir=str(tmp_path / "archive"),
        **config_kwargs,
    )
    transport = StubTransport(responses, config=config)
    telemetry = Telemetry(config.telemetry_file, archive_dir=config.telemetry_archive)
    return Orchestrator(
        config=config,
        transport=transport,
        telemetry=telemetry,
        cache=LRUTTLCache(max_size=config.cache_size, ttl_sec=config.cache_ttl_sec),
        executor=Executor(
            config, transport=transport, telemetry=telemetry, sleep=lambda _seconds: None
        ),
    )


def test_second_identical_call_is_served_from_cache(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path)
    prompt = "Что такое TTL в кеше?"

    first = orchestrator.orchestrate(prompt)
    second = orchestrator.orchestrate(prompt)

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.text == first.text
    assert len(orchestrator.transport.calls) == 1  # второй раз в модель не ходили
    assert orchestrator.cache_stats()["hits"] == 1


def test_cache_key_separates_prompts(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(
        tmp_path, {"первый": "ответ про первый", "второй": "ответ про второй"}
    )

    first = orchestrator.orchestrate("первый вопрос")
    second = orchestrator.orchestrate("второй вопрос")

    assert first.text != second.text
    assert second.cache_hit is False


def test_low_confidence_answer_escalates_to_deep(tmp_path: Path) -> None:
    # StubTransport со списком отдаёт ответы по порядку вызовов: сначала отказ,
    # затем — развёрнутый ответ deep-уровня.
    orchestrator = make_orchestrator(
        tmp_path,
        [
            "Не знаю.",
            "Hit rate 42%: 4200 попаданий из 10000 запросов, LRU вытеснил 318 записей.",
        ],
    )

    result = orchestrator.orchestrate("Сводка по кешу", task="general")

    assert result.escalated is True
    assert result.tier == "deep"
    assert len(result.attempts) == 2
    assert result.outcome == "escalated"
    assert result.confidence >= 0.6
    assert "42%" in result.text


def test_confident_fast_answer_does_not_escalate(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(
        tmp_path,
        "Hit rate 42%: 4200 попаданий из 10000 запросов, латентность 120 мс, всё стабильно.",
    )

    result = orchestrator.orchestrate("Что такое TTL в кеше?")

    assert result.escalated is False
    assert result.tier == "fast"
    assert result.outcome == "ok"
    assert len(result.attempts) == 1


def test_failed_call_returns_error_result(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path, TransportHTTPError(400, "bad request"))

    result = orchestrator.orchestrate("Что такое TTL?")

    assert result.ok is False
    assert result.outcome == "error"
    assert result.text == ""
    assert "400" in (result.error or "")
    # Ошибочный ответ в кеш не попадает.
    assert orchestrator.cache_stats()["size"] == 0


def test_vision_branch_uses_image_hash_in_cache_key(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(
        tmp_path, "На картинке деревянный стул у окна, светлая комната."
    )
    image_a = b"\x89PNG\r\n\x1a\naaa"
    image_b = b"\x89PNG\r\n\x1a\nbbb"

    first = orchestrator.orchestrate("Что на картинке?", task="vision", image=image_a)
    cached = orchestrator.orchestrate("Что на картинке?", task="vision", image=image_a)
    other = orchestrator.orchestrate("Что на картинке?", task="vision", image=image_b)

    assert first.tier == "vision"
    assert first.model == "MiniMax-Vision-32B"
    assert cached.cache_hit is True
    assert other.cache_hit is False, "другая картинка не должна брать ответ из кеша"


def test_telemetry_records_full_flow(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path, "Ответ с числом 42 и достаточной длиной текста.")

    orchestrator.orchestrate("Что такое TTL?", task="docs")
    orchestrator.orchestrate("Что такое TTL?", task="docs")

    records = [item for item in orchestrator.telemetry.records() if item["task"] == "docs"]
    assert len(records) == 2
    assert records[0]["cache_hit"] is False
    assert records[1]["cache_hit"] is True
    assert records[0]["decision"]["tier"] in {"fast", "deep"}
    assert records[0]["model"]

    summary = orchestrator.telemetry.summary()
    assert summary["total"] >= 2
    assert 0.0 < summary["hit_rate"] <= 1.0


def test_selftest_runs_offline(tmp_path: Path) -> None:
    config = RouterConfig(
        telemetry_path=str(tmp_path / "telemetry.jsonl"),
        telemetry_archive_dir=str(tmp_path / "archive"),
        cache_persist=False,
    )

    assert selftest(config, verbose=False) == 0
    assert (tmp_path / "telemetry.jsonl").is_file()


def test_attempts_are_serializable(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path)
    result = orchestrator.orchestrate("Что такое TTL?")

    payload = result.to_dict()
    assert isinstance(payload["attempts"], list)
    assert payload["attempts"][0]["ok"] is True
    assert payload["classification"]["strategy"] == "rule"
