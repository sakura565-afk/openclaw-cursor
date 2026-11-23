"""Тесты scripts.cluster.runner с подменой urllib.request.urlopen."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from scripts.cluster.peers import Peer
from scripts.cluster.runner import RunResult, ensure_bot_chat_session, run_task


def _peer() -> Peer:
    return Peer(
        name="home",
        base_url="https://example.ts.net",
        profile="home",
        api_key="test-key",
        machine_label="home",
        hostname="DESKTOP-6ECJBT6",
    )


class _FakeResponse:
    def __init__(self, payload: dict[str, Any] | list[Any], *, code: int = 200) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self._code = code

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self._code

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_ensure_bot_chat_session_reuses_existing() -> None:
    peer = _peer()
    calls: list[str] = []

    def fake_urlopen(req, timeout=0):  # noqa: ANN001
        calls.append(req.full_url)
        if "sessions?" in req.full_url:
            return _FakeResponse([{"id": "sess-1", "title": "Bot Chat"}])
        raise AssertionError(f"unexpected url: {req.full_url}")

    with patch("scripts.cluster.runner.urlopen", side_effect=fake_urlopen):
        sid = ensure_bot_chat_session(peer, timeout=5)
    assert sid == "sess-1"
    assert len(calls) == 1


def test_run_task_completed_with_output(monkeypatch: pytest.MonkeyPatch) -> None:
    peer = _peer()
    state = {"polls": 0}

    def fake_urlopen(req, timeout=0):  # noqa: ANN001
        url = req.full_url
        if "sessions?" in url:
            return _FakeResponse({"sessions": [{"session_id": "s99", "title": "Bot Chat"}]})
        if url.endswith("/v1/runs") and req.method == "POST":
            return _FakeResponse({"run_id": "run-42"})
        if "/v1/runs/run-42" in url:
            state["polls"] += 1
            return _FakeResponse({"status": "completed", "output": "готово"})
        raise AssertionError(url)

    monkeypatch.setattr("scripts.cluster.runner.time.sleep", lambda _: None)

    with patch("scripts.cluster.runner.urlopen", side_effect=fake_urlopen):
        result = run_task(peer, "ping", wait=True, timeout=30, poll=0.01)

    assert isinstance(result, RunResult)
    assert result.run_id == "run-42"
    assert result.status == "completed"
    assert result.output == "готово"
    assert result.error is None


def test_run_task_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    peer = _peer()

    def fake_urlopen(req, timeout=0):  # noqa: ANN001
        url = req.full_url
        if "sessions?" in url:
            return _FakeResponse([{"id": "s1", "title": "Bot Chat"}])
        if url.endswith("/v1/runs"):
            return _FakeResponse({"run_id": "run-slow"})
        if "/v1/runs/run-slow" in url:
            return _FakeResponse({"status": "running"})
        raise AssertionError(url)

    t = {"now": 0.0}

    def fake_monotonic() -> float:
        return t["now"]

    def bump_sleep(_: float) -> None:
        t["now"] += 5.0

    monkeypatch.setattr("scripts.cluster.runner.time.monotonic", fake_monotonic)
    monkeypatch.setattr("scripts.cluster.runner.time.sleep", bump_sleep)

    with patch("scripts.cluster.runner.urlopen", side_effect=fake_urlopen):
        result = run_task(peer, "wait", wait=True, timeout=10, poll=1)

    assert result.status == "timeout"
    assert result.error


def test_run_task_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    peer = _peer()

    def fake_urlopen(req, timeout=0):  # noqa: ANN001
        url = req.full_url
        if "sessions?" in url:
            return _FakeResponse([{"id": "s1", "title": "Bot Chat"}])
        if url.endswith("/v1/runs"):
            return _FakeResponse({"run_id": "run-err"})
        return _FakeResponse({"status": "failed", "error": "agent crashed"})

    monkeypatch.setattr("scripts.cluster.runner.time.sleep", lambda _: None)

    with patch("scripts.cluster.runner.urlopen", side_effect=fake_urlopen):
        result = run_task(peer, "fail", wait=True, timeout=30, poll=0.01)

    assert result.status == "failed"
    assert result.error == "agent crashed"


def test_run_task_missing_output_terminal_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Терминальный статус без output — run всё равно завершается."""
    peer = _peer()

    def fake_urlopen(req, timeout=0):  # noqa: ANN001
        url = req.full_url
        if "sessions?" in url:
            return _FakeResponse([{"id": "s1", "title": "Bot Chat"}])
        if url.endswith("/v1/runs"):
            return _FakeResponse({"run_id": "run-x"})
        return _FakeResponse({"status": "succeeded", "output": None})

    monkeypatch.setattr("scripts.cluster.runner.time.sleep", lambda _: None)

    with patch("scripts.cluster.runner.urlopen", side_effect=fake_urlopen):
        result = run_task(peer, "ok", wait=True, timeout=30, poll=0.01)

    assert result.status == "succeeded"
    assert result.output is None