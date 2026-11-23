"""Мок urllib: submit, history, таймаут."""

from __future__ import annotations

import json
from typing import Any

import pytest

from scripts.furniture_local.comfy_client import ComfyUIClient, ComfyUIError, pick_workflow
from scripts.furniture_local.config import MachineProfile, ModelBundle


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def test_submit_workflow_returns_prompt_id(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_open(req: Any, timeout: float = 0) -> _FakeResponse:
        calls.append(req.full_url)
        if req.full_url.endswith("/prompt") and req.data:
            return _FakeResponse(json.dumps({"prompt_id": "abc-123"}).encode())
        raise AssertionError(f"unexpected {req.full_url}")

    client = ComfyUIClient("http://127.0.0.1:8188", opener=fake_open)
    pid = client.submit_workflow({"1": {"class_type": "SaveImage", "inputs": {}}})
    assert pid == "abc-123"
    assert any("/prompt" in u for u in calls)


def test_wait_for_result_reads_history(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"n": 0}

    def fake_open(req: Any, timeout: float = 0) -> _FakeResponse:
        if "/history/pid-1" in req.full_url:
            state["n"] += 1
            if state["n"] < 2:
                return _FakeResponse(b"{}")
            payload = {
                "pid-1": {
                    "status": {"completed": True},
                    "outputs": {"13": {"images": [{"filename": "x.png", "type": "output"}]}},
                }
            }
            return _FakeResponse(json.dumps(payload).encode())
        raise AssertionError(req.full_url)

    client = ComfyUIClient(
        "http://127.0.0.1:8188",
        opener=fake_open,
        poll_interval=0.001,
    )
    entry = client.wait_for_result("pid-1", timeout_sec=5.0)
    assert entry["outputs"]["13"]["images"][0]["filename"] == "x.png"


def test_wait_for_result_timeout_message() -> None:
    def fake_open(req: Any, timeout: float = 0) -> _FakeResponse:
        return _FakeResponse(b"{}")

    client = ComfyUIClient("http://127.0.0.1:8188", opener=fake_open, poll_interval=0.001)
    with pytest.raises(ComfyUIError, match="Истекло ожидание"):
        client.wait_for_result("never", timeout_sec=0.05)


def test_pick_workflow_defaults_to_qwen() -> None:
    profile = MachineProfile("local", "http://127.0.0.1:8188", M1 := ModelBundle(unet="u.safetensors"))
    choice = pick_workflow(
        {"furniture_type": "chair", "operation": "color"},
        profile,
        src_name="a.png",
        mask_name="b.png",
    )
    assert choice.kind == "qwen_masked_inpaint"
    assert choice.prompt["8"]["class_type"] == "VAEEncodeForInpaint"
    assert choice.prompt["5"]["inputs"]["image"] == "a.png"


def test_download_view_query() -> None:
    seen: dict[str, str] = {}

    def fake_open(req: Any, timeout: float = 0) -> _FakeResponse:
        seen["url"] = req.full_url
        return _FakeResponse(b"\x89PNG")

    client = ComfyUIClient("http://127.0.0.1:8188", opener=fake_open)
    data = client.download_view({"filename": "out.png", "subfolder": "", "type": "output"})
    assert data[:4] == b"\x89PNG"
    assert "filename=out.png" in seen["url"]