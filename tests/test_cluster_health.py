"""Парсинг health-проб cluster (офлайн)."""

from __future__ import annotations

import json
from unittest.mock import patch

from scripts.cluster.health import (
    parse_nvidia_smi_csv,
    probe_comfyui_system_stats,
    probe_nvidia_smi,
)


def test_parse_nvidia_smi_csv_typical_line() -> None:
    text = "NVIDIA GeForce RTX 4070 Ti SUPER, 16376, 2048\n"
    parsed = parse_nvidia_smi_csv(text)
    assert parsed["available"] is True
    assert "4070" in parsed["name"]
    assert parsed["vram_total_mib"] == 16376
    assert parsed["vram_used_mib"] == 2048
    assert parsed["vram_free_mib"] == 16376 - 2048


def test_parse_nvidia_smi_csv_empty() -> None:
    parsed = parse_nvidia_smi_csv("\n\n")
    assert parsed["available"] is False


def test_probe_comfyui_system_stats_parses_version() -> None:
    payload = {"system": {"comfyui_version": "0.3.45", "python_version": "3.11.9"}}

    def fake_urlopen(req, timeout=0):  # noqa: ANN001
        class R:
            def read(self) -> bytes:
                return json.dumps(payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

        return R()

    with patch("scripts.cluster.health.urlopen", side_effect=fake_urlopen):
        info = probe_comfyui_system_stats("http://127.0.0.1:8188")

    assert info["reachable"] is True
    assert info["comfyui_version"] == "0.3.45"


def test_probe_nvidia_smi_no_binary(monkeypatch) -> None:
    monkeypatch.setattr("scripts.cluster.health.shutil.which", lambda _: None)
    info = probe_nvidia_smi()
    assert info["available"] is False
    assert "не найден" in info["reason"]