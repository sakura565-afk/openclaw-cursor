"""Reject machine-specific absolute paths before they reach another CI host."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Narrow, reviewed exceptions. Keys are (repository-relative file, exact source
# fragment), not broad directories or prefixes. Test fixtures model parser input and
# Windows process data without touching the filesystem. Script entries document
# pre-existing workstation defaults/examples; they are grandfathered individually so
# any new path still fails. Drive prefixes are split in this file to keep the guard
# from matching its own allowlist.
ALLOWLIST: dict[tuple[str, str], str] = {
    # Existing configurable model directory; changing script behavior is out of scope.
    ('scripts/comfy_video_pipeline.py', 'C' + ':\\Users\\user\\comfyui\\ComfyUI_windows_portable\\ComfyUI\\models\\frame_interpolation'): "existing workstation default",

    # Existing model-file defaults in the face-swap workflow.
    ('scripts/face_swap_batch.py', 'C' + ':\\Users\\user\\comfyui\\ComfyUI_windows_portable\\ComfyUI\\models\\facerestore_models\\GFPGANv1.3.pth'): "existing workstation default",
    ('scripts/face_swap_batch.py', 'C' + ':\\Users\\user\\comfyui\\ComfyUI_windows_portable\\ComfyUI\\models\\insightface\\buffalo_l'): "existing workstation default",
    ('scripts/face_swap_batch.py', 'C' + ':\\Users\\user\\comfyui\\ComfyUI_windows_portable\\ComfyUI\\models\\reactor\\faces\\inswapper_128.onnx'): "existing workstation default",

    # Existing CLI example and environment-overridable FreeVideo defaults/launcher.
    ('scripts/freevideo_lora_run.py', 'C' + ':/Users' + '/user/AppData/Local/hermes/cache/scratch'): "existing workstation default",
    ('scripts/freevideo_lora_run.py', 'C' + ':/path/name.mp4'): "existing CLI example",
    ('scripts/freevideo_lora_run.py', 'C' + ':\\Users\\user\\AppData\\Local\\hermes\\scripts\\start_lab_freevideo.py'): "existing launcher default",
    ('scripts/freevideo_lora_run.py', 'C' + ':\\Users\\user\\AppData\\Local\\hermes\\tools\\ffmpeg-9.0.1-win32-x64\\bin\\ffprobe.exe'): "existing workstation default",
    ('scripts/freevideo_lora_run.py', 'E' + ':/ComfyUI_LAB_037/ComfyUI_windows_portable/ComfyUI'): "existing workstation default",
    ('scripts/freevideo_lora_run.py', 'G' + ':/FreeVideo_output'): "existing workstation default",

    # Existing environment-overridable H3 input/output and ffmpeg defaults.
    ('scripts/h3_i2v_run.py', 'C' + ':\\Users\\user\\AppData\\Local\\Microsoft\\WinGet\\Packages\\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\\ffmpeg-9.0.1-full_build\\bin\\ffmpeg.exe'): "existing workstation default",
    ('scripts/h3_i2v_run.py', 'E' + ':\\ComfyUI_windows_portable_nvidia_cu126\\ComfyUI_windows_portable\\ComfyUI\\input'): "existing workstation default",
    ('scripts/h3_i2v_run.py', 'E' + ':\\ComfyUI_windows_portable_nvidia_cu126\\ComfyUI_windows_portable\\ComfyUI\\output'): "existing workstation default",

    # Existing command-line documentation example.
    ('scripts/h3_preflight.py', 'E' + ':/AI/models/comfyui/loras'): "existing CLI example",

    # Existing fixed drive roots probed by the workstation health dashboard.
    ('scripts/health_dashboard.py', 'C' + ':\\\\'): "existing drive root",
    ('scripts/health_dashboard.py', 'E' + ':\\\\'): "existing drive root",
    ('scripts/health_dashboard.py', 'H' + ':\\\\'): "existing drive root",
    ('scripts/health_dashboard.py', 'Q' + ':\\\\'): "existing drive root",

    # Existing local helper defaults and documentation; retained without behavior edits.
    ('scripts/hf_trending.py', 'C' + ':\\Users\\user\\.openclaw\\skills\\telegram-media-send\\scripts\\telegram_media_send_v2.py'): "existing helper default",
    ('scripts/lora_selector.py', 'E' + ':\\AI\\models\\comfyui'): "existing model-root default",
    ('scripts/media_tool.py', 'C' + ':\\\\Users\\\\...'): "documentation example",
    ('scripts/metrika_costs_margin.py', 'C' + ':\\Users\\user\\.openclaw\\skills\\telegram-media-send\\scripts\\telegram_media_send_v2.py'): "existing helper default",
    ('scripts/nouz_common.py', 'E' + ':\\Obsidianstore'): "existing vault default",
    ('scripts/nouz_yaml_tagger.py', 'E' + ':\\\\Obsidianstore).'): "existing CLI help text",
    ('scripts/obsidian_dashboard.py', 'C' + ':\\Users\\user\\Documents\\Obsidian Vault'): "existing vault default",
    ('scripts/obsidian_link_checker.py', 'C' + ':\\Users\\user\\Documents\\Obsidian Vault'): "existing vault default",
    ('scripts/ollama_daily_smoke.py', 'C' + ':\\Users\\user\\.openclaw\\agents\\tasks\\workspace\\scripts\\telegram_config.json'): "existing config default",
    ('scripts/ollama_daily_smoke.py', 'C' + ':\\Users\\user\\.openclaw\\skills\\telegram-media-send\\scripts\\telegram_media_send_v2.py'): "existing helper default",
    ('scripts/ollama_manifest_fix.py', 'H' + ':\\\\ollama\\\\models'): "documentation example",

    # Existing model discovery roots.
    ('scripts/quality_gate.py', 'C' + ':\\Users\\user\\comfyui\\ComfyUI_windows_portable\\ComfyUI\\models'): "existing model-root default",
    ('scripts/quality_gate.py', 'D' + ':\\AI\\Models\\comfyui'): "existing model-root default",
    ('scripts/quality_gate.py', 'E' + ':\\AI\\models\\comfyui'): "existing model-root default",
    ('scripts/queue_manager.py', 'C' + ':\\Users\\user\\.openclaw\\workspace\\memory'): "existing memory-directory default",

    # Existing environment-overridable render output and scratch defaults.
    ('scripts/qwen_edit_run.py', 'C' + ':\\Users\\user\\AppData\\Local\\hermes\\cache\\scratch'): "existing workstation default",
    ('scripts/qwen_edit_run.py', 'E' + ':\\ComfyUI_windows_portable_nvidia_cu126\\ComfyUI_windows_portable\\ComfyUI\\output'): "existing workstation default",
    ('scripts/rife_interpolate_48.py', 'C' + ':\\Users\\user\\AppData\\Local\\hermes\\tools\\ffmpeg-9.0.1-win32-x64\\bin\\ffmpeg.exe'): "existing workstation default",
    ('scripts/rife_interpolate_48.py', 'C' + ':\\Users\\user\\AppData\\Local\\hermes\\tools\\ffmpeg-9.0.1-win32-x64\\bin\\ffprobe.exe'): "existing workstation default",

    # Existing local logging, launcher and ffmpeg defaults in the upscale workflow.
    ('scripts/upscale_video.py', 'C' + ':\\Users\\user\\AppData\\Local\\hermes\\logs'): "existing log-directory default",
    ('scripts/upscale_video.py', 'C' + ':\\Users\\user\\AppData\\Local\\hermes\\scripts\\start_lab_freevideo.py'): "existing launcher default",
    ('scripts/upscale_video.py', 'C' + ':\\Users\\user\\AppData\\Local\\hermes\\tools\\ffmpeg-9.0.1-win32-x64\\bin\\ffprobe.exe'): "existing workstation default",
    ('scripts/upscale_video.py', 'E' + ':\\\\ComfyUI_windows_portable_nvidia_cu126\\\\ComfyUI_windows_portable'): "existing PowerShell default",
    ('scripts/upscale_video.py', 'E' + ':\\\\ComfyUI_windows_portable_nvidia_cu126\\\\ComfyUI_windows_portable\\\\python_embeded\\\\python.exe'): "existing PowerShell default",
    ('scripts/video_assembler.py', 'C' + ':\\\\Users\\\\...'): "documentation example",

    # Synthetic claim-parser input and expected values; no filesystem access occurs.
    ('tests/test_factcheck.py', 'C' + ':\\\\Users\\\\user\\\\models\\\\loras\\\\a.safetensors'): "synthetic parser fixture",
    ('tests/test_factcheck.py', 'C' + ':\\\\dir\\\\file.py'): "synthetic parser fixture",
    ('tests/test_factcheck.py', 'C' + ':\\\\dir\\\\file.py подробнее'): "synthetic parser fixture",
    ('tests/test_factcheck.py', 'C' + ':\\\\gone\\\\x.py\\'): "synthetic diff fixture",
    ('tests/test_factcheck.py', 'C' + ':\\\\here\\\\y.py\\'): "synthetic diff fixture",
    ('tests/test_factcheck.py', 'C' + ':\\\\path\\\\in\\\\comment.py'): "synthetic parser fixture",
    ('tests/test_factcheck.py', 'C' + ':\\definitely\\not\\here\\file.txt'): "synthetic missing-file fixture",

    # Synthetic configuration expectation only.
    ('tests/test_freevideo_director.py', 'G' + ':/FreeVideo_output'): "synthetic config fixture",

    # Synthetic ffmpeg path-conversion and concat-list input/output.
    ('tests/test_video_assembler.py', 'C' + ':/Users' + '/user/media/clip.mp4'): "synthetic ffmpeg fixture",
    ('tests/test_video_assembler.py', 'C' + ':/clips/a.mp4'): "synthetic concat fixture",
    ('tests/test_video_assembler.py', 'C' + ':/clips/b.mp4'): "synthetic concat fixture",
    ('tests/test_video_assembler.py', 'C' + ':\\Users\\user\\media\\clip.mp4'): "synthetic ffmpeg fixture",

    # Synthetic Windows process names, command lines and working directories.
    ('tests/test_vram_guard.py', 'C' + ':/Program Files/app.exe'): "synthetic process fixture",
    ('tests/test_vram_guard.py', 'C' + ':/llamacpp/llama-server.exe'): "synthetic process fixture",
    ('tests/test_vram_guard.py', 'C' + ':/scripts'): "synthetic process fixture",
    ('tests/test_vram_guard.py', 'C' + ':/x'): "synthetic process fixture",
    ('tests/test_vram_guard.py', 'C' + ':/x/11.exe'): "synthetic process fixture",
    ('tests/test_vram_guard.py', 'C' + ':/x/l.exe'): "synthetic process fixture",
    ('tests/test_vram_guard.py', 'C' + ':/x/{pid}.exe'): "synthetic process fixture",
    ('tests/test_vram_guard.py', 'C' + ':\\Users\\user\\llamacpp\\llama-server.exe'): "synthetic process-name fixture",
}

# The boundary before a drive letter prevents matches inside schemes such as https.
# POSIX home roots require a user component; the other POSIX forms are WSL/MSYS roots.
ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"[A-Za-z]:[\\/][^\"'`\r\n<>|]*"
    + r"|" + "/home" + r"/[^/\s\"'`<>|]+/[^\"'`\r\n<>|]*"
    + r"|" + "/Users" + r"/[^/\s\"'`<>|]+/[^\"'`\r\n<>|]*"
    + r"|" + "/mnt" + r"/c/[^\"'`\r\n<>|]*"
    + r"|" + "/c" + "/Users" + r"/[^\"'`\r\n<>|]*"
    + r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    fragment: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.fragment!r}"


def scan_file(
    file_path: Path,
    *,
    root: Path,
    allowlist: Mapping[tuple[str, str], str] = ALLOWLIST,
) -> list[Finding]:
    """Return unallowlisted absolute-path fragments in one Python source file."""
    relative_path = file_path.relative_to(root).as_posix()
    findings: list[Finding] = []
    for line_number, line in enumerate(
        file_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        for match in ABSOLUTE_PATH_RE.finditer(line):
            fragment = match.group(0).rstrip()
            if (relative_path, fragment) not in allowlist:
                findings.append(Finding(relative_path, line_number, fragment))
    return findings


def scan_roots(
    roots: tuple[Path, ...],
    *,
    root: Path = REPO_ROOT,
    allowlist: Mapping[tuple[str, str], str] = ALLOWLIST,
) -> list[Finding]:
    """Recursively scan only Python files below the requested roots."""
    findings: list[Finding] = []
    for scan_root in roots:
        for file_path in sorted(scan_root.rglob("*.py")):
            findings.extend(scan_file(file_path, root=root, allowlist=allowlist))
    return findings


def test_repository_has_no_unreviewed_machine_specific_paths() -> None:
    findings = scan_roots((REPO_ROOT / "tests", REPO_ROOT / "scripts"))
    assert not findings, (
        "Machine-specific absolute paths break CI on other hosts. Use tmp_path, "
        "Path(__file__), environment configuration, or add a narrow reviewed "
        "allowlist entry for synthetic test data:\n"
        + "\n".join(str(finding) for finding in findings)
    )


@pytest.mark.parametrize(
    "machine_path",
    [
        "E" + ":/tmp/openclaw-cursor-test/scripts/freevideo_lora_run.py",
        "E" + r":\tmp\openclaw-cursor-test\scripts\freevideo_lora_run.py",
        "C" + ":" + "/Users" + "/user/AppData/Local/hermes",
        "C" + r":\Users\user\AppData\Local\hermes",
        "/home" + "/alice/models/model.safetensors",
        "/Users" + "/alice/models/model.safetensors",
        "/mnt" + "/c/models/model.safetensors",
        "/c" + "/Users" + "/user/models/model.safetensors",
    ],
)
def test_scanner_detects_absolute_path(tmp_path: Path, machine_path: str) -> None:
    source = tmp_path / "sample.py"
    source.write_text(f'value = "{machine_path}"\n', encoding="utf-8")

    findings = scan_file(source, root=tmp_path, allowlist={})

    assert [(f.path, f.line, f.fragment) for f in findings] == [
        ("sample.py", 1, machine_path)
    ]


def test_scanner_recurses_and_ignores_non_python_files(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    machine_path = "E" + ":/tmp/openclaw-cursor-test"
    (nested / "bad.py").write_text(f'value = "{machine_path}"\n', encoding="utf-8")
    (nested / "ignored.txt").write_text(machine_path, encoding="utf-8")

    findings = scan_roots((tmp_path,), root=tmp_path, allowlist={})

    assert [finding.path for finding in findings] == ["nested/bad.py"]


def test_scanner_ignores_urls_and_relative_paths(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        'url = "https://example.com/home/alice/file"\n'
        'relative = "tmp/openclaw-cursor-test/file.py"\n',
        encoding="utf-8",
    )

    assert scan_file(source, root=tmp_path, allowlist={}) == []


def test_allowlist_applies_only_to_exact_file_and_fragment(tmp_path: Path) -> None:
    machine_path = "C" + r":\Users\user\llamacpp\llama-server.exe"
    allowlist = {("sample.py", machine_path): "synthetic process-name fixture"}
    source = tmp_path / "sample.py"
    source.write_text(f'value = r"{machine_path}"\n', encoding="utf-8")

    assert scan_file(source, root=tmp_path, allowlist=allowlist) == []

    other_file = tmp_path / "other.py"
    other_file.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    assert [finding.fragment for finding in scan_file(other_file, root=tmp_path, allowlist=allowlist)] == [
        machine_path
    ]

    source.write_text(f'value = r"{machine_path}.other"\n', encoding="utf-8")
    assert [finding.fragment for finding in scan_file(source, root=tmp_path, allowlist=allowlist)] == [
        machine_path + ".other"
    ]


def test_allowlist_entries_still_match_repository_source() -> None:
    all_findings = scan_roots(
        (REPO_ROOT / "tests", REPO_ROOT / "scripts"), allowlist={}
    )
    found_keys = {(finding.path, finding.fragment) for finding in all_findings}
    stale = sorted(set(ALLOWLIST) - found_keys)
    assert not stale, f"Remove or update stale allowlist entries: {stale!r}"
