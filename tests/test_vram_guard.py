"""Unit tests for the VRAM guard (no GPU required)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from video_pipeline import vram_guard
from video_pipeline.vram_guard import (
    HeldProcess,
    list_gpu_processes,
    pause_named_processes,
    restore_processes,
)


@pytest.fixture(autouse=True)
def _windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend we are on Windows so the guard logic is exercised on any CI platform."""
    monkeypatch.setattr(vram_guard, "IS_WINDOWS", True)


def test_list_gpu_processes_parses_nvidia_smi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vram_guard,
        "_run",
        lambda cmd, timeout=30: "1234, llama-server.exe\n2345, chrome.exe\n",
    )
    procs = list_gpu_processes()
    assert [(p.pid, p.name) for p in procs] == [(1234, "llama-server.exe"), (2345, "chrome.exe")]


def test_list_gpu_processes_ignores_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vram_guard, "_run", lambda cmd, timeout=30: "garbage\n\n1,2\n")
    assert list_gpu_processes() == []


def test_pause_named_processes_only_matches_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vram_guard,
        "list_gpu_processes",
        lambda: [HeldProcess(pid=11, name="llama-server.exe"), HeldProcess(pid=22, name="chrome.exe")],
    )
    monkeypatch.setattr(vram_guard, "_process_details", lambda pid: (f"C:/x/{pid}.exe", [f"C:/x/{pid}.exe"], "C:/x"))
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: int = 30) -> str:
        calls.append(cmd)
        return "OK"

    monkeypatch.setattr(vram_guard, "_run", fake_run)
    stopped = pause_named_processes(["llama-server.exe"])
    assert [p.pid for p in stopped] == [11]
    assert stopped[0].cmdline == ["C:/x/11.exe"]
    assert any(c[:2] == ["taskkill", "/PID"] for c in calls)
    assert not any("22" in " ".join(c) for c in calls if c[:2] == ["taskkill", "/PID"])


def test_pause_named_processes_noop_without_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vram_guard, "list_gpu_processes", lambda: [HeldProcess(pid=11, name="llama-server.exe")]
    )
    assert pause_named_processes([]) == []


def test_pause_named_processes_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vram_guard, "list_gpu_processes", lambda: [HeldProcess(pid=9, name="Llama-Server.EXE")]
    )
    monkeypatch.setattr(vram_guard, "_process_details", lambda pid: ("C:/x/l.exe", ["C:/x/l.exe"], ""))
    monkeypatch.setattr(vram_guard, "_run", lambda cmd, timeout=30: "OK")
    assert [p.pid for p in pause_named_processes(["llama-server.exe"])] == [9]


def test_restore_processes_relaunches_with_same_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[tuple[list[str], str]] = []

    def fake_popen(args, cwd=None, **kwargs):
        launched.append((list(args), cwd or ""))
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    stopped = [
        HeldProcess(
            pid=5,
            name="llama-server.exe",
            exe_path="C:/llamacpp/llama-server.exe",
            cmdline=["C:/llamacpp/llama-server.exe", "--port", "18434"],
            cwd="C:/scripts",
        )
    ]
    assert restore_processes(stopped) == [5]
    assert launched[0][0] == ["C:/llamacpp/llama-server.exe", "--port", "18434"]
    assert launched[0][1] == "C:/scripts"


def test_restore_processes_skips_without_cmdline() -> None:
    assert restore_processes([HeldProcess(pid=6, name="x.exe")]) == []


def test_split_windows_command_line_handles_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vram_guard, "IS_WINDOWS", False)
    parts = vram_guard._split_windows_command_line('"C:/Program Files/app.exe" --flag "a b"')
    assert parts[-1] == "a b"
    assert any("app.exe" in p for p in parts)


def test_cli_parser_exposes_pause_process() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "product_shot_cli",
        Path(__file__).resolve().parents[1] / "scripts" / "product_shot.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = module.build_parser().parse_args(
        [
            "--image", "a.png",
            "--width", "736",
            "--height", "1024",
            "--prompt", "p.txt",
            "--out", "o.mp4",
            "--pause-process", "llama-server.exe",
        ]
    )
    assert args.pause_process == ["llama-server.exe"]


def test_pause_matches_full_executable_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """nvidia-smi reports full paths on Windows - matching must use the file name."""
    monkeypatch.setattr(vram_guard, "IS_WINDOWS", True)
    monkeypatch.setattr(
        vram_guard,
        "list_gpu_processes",
        lambda: [
            HeldProcess(pid=77, name=r"C:\Users\user\llamacpp\llama-server.exe"),
        ],
    )
    monkeypatch.setattr(vram_guard, "_process_details", lambda pid: (f"C:/x/{pid}.exe", [f"C:/x/{pid}.exe"], ""))
    monkeypatch.setattr(vram_guard, "_run", lambda cmd, timeout=30: "OK")
    assert [p.pid for p in pause_named_processes(["llama-server.exe"])] == [77]
