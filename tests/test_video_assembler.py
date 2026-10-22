"""Тесты scripts/video_assembler.py.

Сетевых вызовов нет: ffmpeg/ffprobe замоканы через подмену
``subprocess.run``. Часть тестов помечена ``real_ffmpeg`` — они
пропускаются, если ffmpeg недоступен.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import video_assembler as va  # noqa: E402


# ---------------------------------------------------------------------------
# Фикстуры и заглушки
# ---------------------------------------------------------------------------


class FakeCompleted:
    """Результат subprocess.run без text=True — stdout/stderr это bytes."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout.encode("utf-8")
        self.stderr = stderr.encode("utf-8")


def _ffprobe_video(width=768, height=922, fps="24/1", frames=141, duration=5.875, audio=False):
    payload = {
        "streams": [
            {
                "width": width,
                "height": height,
                "r_frame_rate": fps,
                "nb_frames": str(frames),
                "codec_name": "h264",
            }
        ],
        "format": {"duration": str(duration)},
    }
    if audio:
        payload["streams"].append({"sample_rate": "48000", "channels": 2})
    return json.dumps(payload)


def install_fake_ffprobe(monkeypatch, video_json=None, audio_json=None, returncode=0):
    """Подменить ffprobe: по содержимому команды решаем, что вернуть."""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        joined = " ".join(str(c) for c in cmd)
        if "ffprobe" in joined:
            if "-select_streams" in joined and "a:0" in joined:
                return FakeCompleted(returncode, audio_json if audio_json is not None else json.dumps({"streams": []}))
            if "-select_streams" in joined and "stream=codec_name" in joined:
                return FakeCompleted(returncode, json.dumps({"streams": [{"codec_name": "aac"}]}))
            return FakeCompleted(
                returncode,
                video_json if video_json is not None else _ffprobe_video(),
            )
        return FakeCompleted(0, "")

    monkeypatch.setattr(va.subprocess, "run", fake_run)
    return calls


def install_fake_ffmpeg(monkeypatch, returncode=0, stderr="", video_json=None):
    """Подменить ffmpeg: команда «успешна», выходной файл создаётся.

    ffprobe обслуживается тем же моком — иначе validate() после ffmpeg
    получал бы настоящий ffprobe и падал бы на файле-заглушке.
    """
    seen = []

    def fake_run(cmd, *args, **kwargs):
        seen.append(list(cmd))
        joined = " ".join(str(c) for c in cmd)
        if "ffprobe" in joined:
            if "-select_streams" in joined and "a:0" in joined:
                return FakeCompleted(0, json.dumps({"streams": []}))
            if "-select_streams" in joined and "stream=codec_name" in joined:
                return FakeCompleted(0, json.dumps({"streams": [{"codec_name": "aac"}]}))
            return FakeCompleted(0, video_json if video_json is not None else _ffprobe_video())
        if cmd and cmd[0].endswith("ffmpeg"):
            out = Path(str(cmd[-1]).replace("/", os.sep))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\x00\x00\x00 ftypisom" + b"\x00" * 64)
        return FakeCompleted(returncode, "", stderr)

    monkeypatch.setattr(va.subprocess, "run", fake_run)
    return seen


# ---------------------------------------------------------------------------
# probe / validate
# ---------------------------------------------------------------------------


def test_probe_missing_file_raises_media_error(tmp_path: Path) -> None:
    with pytest.raises(va.MediaError):
        va.probe(tmp_path / "нет-такого.mp4")


def test_probe_broken_file_raises_media_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"\x00\x00\x00 ftypisom")
    with pytest.raises(va.MediaError):
        va.probe(broken)


def test_validate_false_on_zero_byte_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    assert va.validate(empty) is False


def test_validate_false_on_unreadable_mp4(tmp_path: Path) -> None:
    """Оборванный mp4 непустой, но нечитаемый — moov atom not found."""
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"\x00\x00\x00 ftypisom" + b"\x00" * 128)
    with pytest.raises(va.MediaError):
        va.probe(broken)
    assert va.validate(broken) is False


def test_probe_reads_metadata(monkeypatch, tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    install_fake_ffprobe(monkeypatch, _ffprobe_video(width=1920, height=1080, fps="30000/1001"))
    info = va.probe(clip)
    assert info.width == 1920
    assert info.height == 1080
    assert info.fps == pytest.approx(30.0 / 1.001, rel=1e-3)
    assert info.has_audio is False


# ---------------------------------------------------------------------------
# Подбор музыкального окна
# ---------------------------------------------------------------------------


def test_pick_window_skips_leading_silence() -> None:
    """Окно не должно начинаться в тишине."""
    rate = 1000
    silence = [0.0] * (rate * 3)
    loud = [0.5] * (rate * 12)
    start = va.pick_music_window(silence + loud, rate, 5.0)
    assert start >= 2.5


def test_pick_window_raises_when_track_too_short() -> None:
    rate = 1000
    with pytest.raises(va.MediaError):
        va.pick_music_window([0.5] * (rate * 2), rate, 10.0)


def test_trim_music_errors_when_window_exceeds_track(monkeypatch, tmp_path: Path) -> None:
    music = tmp_path / "track.mp3"
    music.write_bytes(b"x")
    install_fake_ffprobe(monkeypatch, _ffprobe_video(duration=3.0))
    with pytest.raises(va.MediaError):
        va.trim_music(music, tmp_path / "out.m4a", 11.75, start=0.0)


# ---------------------------------------------------------------------------
# Пути и аргументы ffmpeg
# ---------------------------------------------------------------------------


def test_ffmpeg_paths_use_forward_slashes() -> None:
    assert va._ff_path(r"C:\Users\user\clip.mp4") == "C:/Users/user/clip.mp4"


def test_normalize_clip_passes_forward_slash_paths(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    seen = install_fake_ffmpeg(monkeypatch)
    va.normalize_clip(src, tmp_path / "out.mp4")  # validate() проходит по моку ffprobe
    assert seen, "ffmpeg не вызван"
    for arg in seen[0]:
        assert "\\" not in str(arg), f"обратный слэш в аргументе: {arg}"


def test_concat_list_written_without_indentation(monkeypatch, tmp_path: Path) -> None:
    """Список concat без отступов: иначе ffmpeg видит путь с пробелами."""
    clips = []
    for i in range(2):
        p = tmp_path / f"c{i}.mp4"
        p.write_bytes(b"x")
        clips.append(p)
    seen = install_fake_ffmpeg(monkeypatch)
    va.concat(clips, tmp_path / "out.mp4", workdir=tmp_path)
    lst = tmp_path / "_concat_list.txt"
    assert lst.exists()
    text = lst.read_text(encoding="utf-8")
    assert text and not text.startswith(" ")
    for line in text.strip().splitlines():
        assert line.startswith("file '")


# ---------------------------------------------------------------------------
# Проверка стыка
# ---------------------------------------------------------------------------


def test_seam_report_flags_visible_seam(monkeypatch) -> None:
    """Резкий скачок контраста должен быть помечен как видимый стык."""
    frames = {
        5.0: (100.0, 50.0),
        5.875: (100.0, 20.0),
        6.25: (100.0, 50.0),
    }

    def fake_stats(path, t, size=128):
        means, stds = frames.get(round(t, 3), (100.0, 50.0))
        return (means, means, means), (stds, stds, stds)

    monkeypatch.setattr(va, "frame_stats", fake_stats)
    report = va.seam_report("video.mp4", 5.875)
    assert report.visible is True
    assert report.std_delta > va.SEAM_STD_THRESHOLD


def test_seam_report_no_flag_on_smooth_video(monkeypatch) -> None:
    def fake_stats(path, t, size=128):
        return (100.0, 100.0, 100.0), (50.0, 50.0, 50.0)

    monkeypatch.setattr(va, "frame_stats", fake_stats)
    report = va.seam_report("video.mp4", 5.875)
    assert report.visible is False


def test_seam_report_uses_std_not_only_mean(monkeypatch) -> None:
    """Среднее совпадает, контраст отличается — стык всё равно виден.

    Именно этот случай ловит ошибку «проверил mean RGB и решил, что стык чист».
    """
    def fake_stats(path, t, size=128):
        if abs(t - 5.875) < 0.01:
            return (100.0, 100.0, 100.0), (50.0, 50.0, 50.0)
        return (100.0, 100.0, 100.0), (65.0, 65.0, 65.0)

    monkeypatch.setattr(va, "frame_stats", fake_stats)
    report = va.seam_report("video.mp4", 5.875)
    assert report.mean_delta == pytest.approx(0.0, abs=0.5)
    assert report.visible is True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        va.main(["--help"])
    assert exc.value.code == 0


def test_cli_without_command_returns_two() -> None:
    assert va.main([]) == 2


def test_cli_probe_reports_valid(tmp_path: Path, monkeypatch, capsys) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    install_fake_ffprobe(monkeypatch)
    assert va.main(["probe", str(clip)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["width"] == 768
    assert payload["valid"] is True


# ---------------------------------------------------------------------------
# Регрессия: media_tool передаёт пути нативной формы
# ---------------------------------------------------------------------------


def test_media_tool_ffmpeg_paths_are_accepted_by_ffmpeg(tmp_path: Path) -> None:
    """Пути с обратными слэшами должны работать на этой сборке ffmpeg.

    Раньше в скилле было записано, что ffmpeg на Windows «не находит путь с
    обратными слэшами и файл просто не появляется». На ffmpeg 8.1
    (полная сборка gyan.dev) это НЕ воспроизводится — и в media_tool.py
    ничего чинить не нужно. Тест фиксирует фактическое поведение, чтобы
    правку не пытались внести снова.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg не установлен")
    src = tmp_path / "src.jpg"
    made = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64",
         "-frames:v", "1", str(src)],
        capture_output=True, timeout=60,
    )
    if made.returncode != 0 or not src.exists():
        pytest.skip("ffmpeg не смог создать тестовый кадр")
    out = tmp_path / "out.jpg"
    res = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(src), "-frames:v", "1", str(out)],
        capture_output=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr.decode("utf-8", "replace")[:300]
    assert out.exists() and out.stat().st_size > 0
