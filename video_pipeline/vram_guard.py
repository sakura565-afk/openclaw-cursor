"""VRAM guard: pause GPU-holding processes that are not ComfyUI before a render.

Why this exists (measured 2026-10-26 on the RTX 4070 Ti SUPER):
the FreeVideo resident worker keeps ~13.4 GB of model weights. When they do not fit in
VRAM the engine keeps them in host RAM and streams them over PCIe for every transformer
block: 0.57 s/block (312 s for a 175-frame 7 s clip) with the weights resident versus
~46 s/block (over 1800 s) with only ~3.75 GB of working headroom - about 80x slower.

`POST /free` only helps ComfyUI instances. A local `llama-server` with GPU offload
(~8 GB for a 27B Q4 GGUF) is not a ComfyUI process and has to be stopped and restarted.

The module is Windows-first but imports cleanly everywhere: on non-Windows platforms
the process-listing calls return an empty list and pausing is a no-op.
"""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"
NVIDIA_SMI = shutil.which("nvidia-smi") or "nvidia-smi"

# Lock file honoured by the local llama-server watchdog: while it exists, the
# watchdog must NOT boot llama-server, otherwise it resurrects the process in the
# middle of a render and takes the VRAM the FreeVideo worker needs.
RENDER_LOCK_PATH = Path(
    os.environ.get(
        "HERMES_RENDER_LOCK",
        r"C:/Users/user/AppData/Local/hermes/cache/scratch/render_vram.lock",
    )
)


def acquire_render_lock(reason: str = "render") -> Path:
    """Create the render lock so supervisors keep the GPU free."""
    RENDER_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    RENDER_LOCK_PATH.write_text(f"{reason} {time.time()}\n", encoding="utf-8")
    return RENDER_LOCK_PATH


def release_render_lock() -> None:
    """Remove the render lock (never raises)."""
    try:
        RENDER_LOCK_PATH.unlink()
    except OSError:
        pass


@dataclass
class HeldProcess:
    """A process that currently holds a CUDA context on the GPU."""

    pid: int
    name: str
    exe_path: str = ""
    cmdline: list[str] = field(default_factory=list)
    cwd: str = ""


def _run(cmd: list[str], timeout: int = 30) -> str:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout or ""


def _split_windows_command_line(command_line: str) -> list[str]:
    """Split a Windows command line into argv (CommandLineToArgvW)."""
    if not command_line:
        return []
    if not IS_WINDOWS:  # pragma: no cover - non-Windows path
        import shlex

        return [tok.strip('"') for tok in shlex.split(command_line, posix=False)]
    argc = ctypes.c_int()
    argv = ctypes.POINTER(ctypes.c_wchar_p)()
    try:
        ctypes.windll.shell32.CommandLineToArgvW(command_line, ctypes.byref(argc))
    except (AttributeError, OSError):
        import shlex

        return [tok.strip('"') for tok in shlex.split(command_line, posix=False)]
    if not argv:
        return command_line.split()
    try:
        return [argv[i] for i in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv)


def list_gpu_processes() -> list[HeldProcess]:
    """Return every process that currently holds a CUDA context on the GPU."""
    if not IS_WINDOWS:  # pragma: no cover - non-Windows path
        return []
    out = _run([NVIDIA_SMI, "--query-compute-apps=pid,process_name", "--format=csv,noheader"])
    found: list[HeldProcess] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        name = parts[1]
        if not name or name.isdigit():
            continue
        found.append(HeldProcess(pid=int(parts[0]), name=name))
    return found


def _process_details(pid: int) -> tuple[str, list[str], str]:
    """Return (exe_path, argv, cwd) for a pid using PowerShell CIM."""
    if not IS_WINDOWS:  # pragma: no cover - non-Windows path
        return "", [], ""
    script = (
        "$p = Get-CimInstance Win32_Process -Filter \"ProcessId=%d\"; "
        "if ($p) { $p.ExecutablePath + '|' + $p.CommandLine + '|' + $p.ExecutablePath }" % pid
    )
    out = _run(["powershell", "-NoProfile", "-Command", script])
    line = out.strip().splitlines()[0] if out.strip() else ""
    if "|" not in line:
        return "", [], ""
    exe, cmdline, _ = line.split("|", 2)
    return exe, _split_windows_command_line(cmdline.strip()), ""


def _exe_basename(name: str) -> str:
    """File name of an executable path, for either separator.

    ``os.path.basename`` only splits on the host separator, so a Windows path
    (``C:\\Users\\...\\llama-server.exe``) survives intact on POSIX hosts and
    never matches a plain process name. Split on both separators explicitly.
    """
    return name.replace("\\", "/").rsplit("/", 1)[-1].lower()


def pause_named_processes(names: list[str]) -> list[HeldProcess]:
    """Stop GPU-holding processes whose executable name matches one of ``names``.

    Returns the stopped processes so they can be restored with
    :func:`restore_processes`. Names are matched case-insensitively against the
    executable file name.
    """
    if not IS_WINDOWS or not names:
        return []
    # nvidia-smi reports the FULL executable path on Windows
    # (e.g. C:\Users\user\llamacpp\llama-server.exe), so match on the file name.
    wanted = {_exe_basename(n) for n in names}
    stopped: list[HeldProcess] = []
    for proc in list_gpu_processes():
        base = _exe_basename(proc.name)
        if base not in wanted:
            continue
        exe, argv, cwd = _process_details(proc.pid)
        proc.exe_path = exe
        proc.cmdline = argv
        proc.cwd = cwd
        if _run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], timeout=20):
            stopped.append(proc)
    return stopped


def restore_processes(processes: list[HeldProcess]) -> list[int]:
    """Restart previously paused processes with their original command line."""
    restarted: list[int] = []
    for proc in processes:
        if not proc.cmdline:
            continue
        exe = proc.exe_path or proc.cmdline[0]
        args = proc.cmdline[1:] if proc.cmdline[0].lower().endswith(".exe") else proc.cmdline
        try:
            subprocess.Popen(
                [exe, *args],
                cwd=proc.cwd or None,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            continue
        restarted.append(proc.pid)
    return restarted


def free_vram_gb(url: str) -> float | None:  # pragma: no cover - thin wrapper
    """Re-exported helper so callers do not need to import two modules."""
    from video_pipeline.upscale import free_vram_gb as _free

    return _free(url)
