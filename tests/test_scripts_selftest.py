from __future__ import annotations

import importlib

import pytest


SCRIPT_MODULES = [
    "scripts.context_split",
    "scripts.media_tool",
    "scripts.memory_analytics",
    "scripts.memory_cleanup",
    "scripts.ollama_batch",
    "scripts.ollama_benchmark",
    "scripts.ollama_model_manager",
    "scripts.ollama_monitor",
    "scripts.optimize_context",
    "scripts.proactive_scout",
    "scripts.queue_manager",
    "scripts.sync_obsidian",
    "scripts.telegram_sender",
]


@pytest.mark.self_test
@pytest.mark.parametrize("module_name", SCRIPT_MODULES)
def test_module_selftest_passes(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert hasattr(module, "_selftest"), f"{module_name} is missing _selftest()"
    assert module._selftest() is True
