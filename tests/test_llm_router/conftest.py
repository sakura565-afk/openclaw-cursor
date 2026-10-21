"""Страховка от сети: любой реальный ``urlopen`` в этих тестах — провал теста."""

from __future__ import annotations

import urllib.request

import pytest


class NetworkAccessDenied(AssertionError):
    """Тест попытался выйти в сеть."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*args: object, **kwargs: object) -> None:
        raise NetworkAccessDenied("тесты llm_router не должны ходить в сеть")

    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
    # Токен подменяем, чтобы resolve_access_token не читал настоящий auth.json.
    monkeypatch.setenv("MINIMAX_ACCESS_TOKEN", "test-token")
