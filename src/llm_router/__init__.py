"""Четырёхслойный LLM-оркестратор: cache -> classifier -> executor -> judge.

Пакет — production-слой над наивными вызовами модели. Слои собираются
в фасаде :mod:`src.llm_router.orchestrator`:

* ``cache``      — LRU+TTL с ключом, учитывающим модель, effort и картинку;
* ``classifier`` — выбор tier (``fast``/``deep``) одной из 4 стратегий;
* ``executor``   — параллельные и каскадные вызовы с ретраями и таймаутами;
* ``judge``      — оценка уверенности ответа и решение об эскалации;
* ``telemetry``  — append-only JSONL с ротацией и агрегатами;
* ``vision``     — прямой вызов vision-модели с обработкой 451 как фолбэка.

Весь транспорт — stdlib ``urllib.request``; секреты берутся только из
``auth.json`` или переменных окружения.

Импорты ленивые (PEP 562): иначе ``python -m src.llm_router.orchestrator``
подтягивал бы модуль дважды и ругался RuntimeWarning.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Dict

#: Публичное имя -> модуль, из которого оно приходит.
_EXPORTS: Dict[str, str] = {
    "Attempt": "executor",
    "CallResult": "transport",
    "CascadeOutcome": "executor",
    "Classification": "classifier",
    "Classifier": "classifier",
    "Executor": "executor",
    "Judgement": "judge",
    "LRUTTLCache": "cache",
    "MiniMaxTransport": "transport",
    "OrchestrationResult": "orchestrator",
    "Orchestrator": "orchestrator",
    "RouterConfig": "config",
    "StubTransport": "transport",
    "Telemetry": "telemetry",
    "TransportError": "transport",
    "TransportHTTPError": "transport",
    "VisionError": "vision",
    "analyze_image": "vision",
    "is_vision_blocked": "vision",
    "judge": "judge",
    "load_config": "config",
    "make_cache_key": "cache",
    "needs_escalation": "judge",
    "orchestrate": "orchestrator",
    "resolve_access_token": "config",
    "vision_fallback": "vision",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"{__name__}.{module_name}"), name)


def __dir__() -> list[str]:
    return list(__all__)
