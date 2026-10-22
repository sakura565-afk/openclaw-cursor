"""Тесты монтажного слоя ``scripts/video_assembler.py``.

Реальный ffmpeg не вызывается: ``subprocess.run`` подменяется мини-эмулятором
:class:`FakeFfmpeg`. Он держит «медиафайлы» как JSON-описания на диске и честно
пересчитывает их параметры по аргументам командной строки, поэтому тесты
проверяют и логику модуля, и то, какие именно аргументы уходят в ffmpeg.

Эмулятор нарочно воспроизводит два реальных капкана:

* нечитаемый файл отвечает ``moov atom not found`` (прерванный ffmpeg
  записывает атом ``moov`` в конце файла);
* AAC-реенкод без ``-ar``/``-ac`` даёт 32 kHz и один канал.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import media_tool  # noqa: E402
from scripts import video_assembler as va  # noqa: E402

MEDIA_MARK = "__fake_media__"

DEFAULT_MEDIA: dict = {
    MEDIA_MARK: True,
    "width": 768,
    "height": 922,
    "fps": 24.0,
    "duration": 5.875,
    "nb_frames": 141,
    "video_codec": "h264",
    "has_video": True,
    "has_audio": False,
    "audio_codec": "",
    "sample_rate": 0,
    "channels": 0,
    # Аудио: список [длительность, амплитуда] — из него эмулятор лепит PCM.
    "segments": [],
    # Видео: список [t_начала, mean, std] — из него эмулятор лепит кадр.
    "levels": [],
}


# --------------------------------------------------------------------------
# Синтетические медиафайлы
# --------------------------------------------------------------------------

# Реестр «медиафайлов» по тому пути, который уходит в ffmpeg (только ``/``).
# Так эмулятор ведёт себя как ffmpeg на Windows: прямые слэши он понимает, а
# тесты с обратными слэшами в имени работают и на POSIX.
REGISTRY: dict[str, Path] = {}


def _dump(path: os.PathLike[str] | str, data: dict) -> Path:
    target = Path(va.native_path(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data), encoding="utf-8")
    REGISTRY[va.ffmpeg_path(path)] = target
    return target


def write_media(path: os.PathLike[str] | str, **overrides) -> Path:
    """Создать «видеофайл» с заданными параметрами."""
    data = dict(DEFAULT_MEDIA)
    data.update(overrides)
    if data["has_video"] and data["fps"]:
        data["nb_frames"] = overrides.get(
            "nb_frames", int(round(data["duration"] * data["fps"]))
        )
    return _dump(path, data)


def write_audio(
    path: os.PathLike[str] | str,
    segments: list[list[float]],
    **overrides,
) -> Path:
    """Создать «аудиофайл» из сегментов [длительность, амплитуда]."""
    data = dict(DEFAULT_MEDIA)
    data.update(
        {
            "has_video": False,
            "width": 0,
            "height": 0,
            "fps": 0.0,
            "nb_frames": 0,
            "video_codec": "",
            "has_audio": True,
            "audio_codec": "mp3",
            "sample_rate": 44100,
            "channels": 2,
            "duration": float(sum(seg[0] for seg in segments)),
            "segments": [[float(d), float(a)] for d, a in segments],
        }
    )
    data.update(overrides)
    return _dump(path, data)


def write_broken(path: os.PathLike[str] | str) -> Path:
    """Непустой, но нечитаемый mp4 — так выглядит прерванный ffmpeg."""
    target = Path(va.native_path(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 128)
    return target


def read_media(path: os.PathLike[str] | str) -> dict | None:
    target = REGISTRY.get(va.ffmpeg_path(path), Path(va.native_path(path)))
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get(MEDIA_MARK) else None


# --------------------------------------------------------------------------
# Разбор командной строки ffmpeg
# --------------------------------------------------------------------------

def _parse_args(args: list[str]) -> dict:
    spec: dict = {
        "inputs": [],
        "lavfi": [],
        "formats": set(),
        "vf": "",
        "af": "",
        "rate": None,
        "ss": None,
        "t": None,
        "ar": None,
        "ac": None,
        "frames": None,
        "an": False,
        "vn": False,
        "shortest": False,
        "output": args[-1],
    }
    pending_lavfi = False
    index = 1
    while index < len(args) - 1:
        token = args[index]
        value = args[index + 1]
        if token == "-i":
            if pending_lavfi or value.startswith("anullsrc"):
                spec["lavfi"].append(value)
                pending_lavfi = False
            else:
                spec["inputs"].append(value)
            index += 2
            continue
        if token == "-f":
            if value == "lavfi":
                pending_lavfi = True
            spec["formats"].add(value)
            index += 2
            continue
        simple = {
            "-vf": "vf",
            "-filter:v": "vf",
            "-af": "af",
        }
        if token in simple:
            spec[simple[token]] = value
            index += 2
            continue
        numeric = {"-r": "rate", "-ss": "ss", "-t": "t"}
        if token in numeric:
            spec[numeric[token]] = float(value)
            index += 2
            continue
        integer = {"-ar": "ar", "-ac": "ac", "-frames:v": "frames"}
        if token in integer:
            spec[integer[token]] = int(value)
            index += 2
            continue
        if token == "-an":
            spec["an"] = True
        elif token == "-vn":
            spec["vn"] = True
        elif token == "-shortest":
            spec["shortest"] = True
        index += 1
    spec["concat"] = "concat" in spec["formats"]
    return spec


def _scale_size(vf: str) -> tuple[int, int] | None:
    match = re.search(r"scale=(\d+):(\d+)", vf)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _filter_fps(vf: str) -> float | None:
    match = re.search(r"fps=(\d+(?:\.\d+)?)", vf)
    return float(match.group(1)) if match else None


def _level_at(data: dict, t: float) -> tuple[float, float]:
    levels = sorted(data.get("levels") or [], key=lambda item: item[0])
    current = (60.0, 12.0)
    for start, mean, std in levels:
        if t + 1e-6 >= start:
            current = (float(mean), float(std))
    return current


def _synth_frame(size: int, mean: float, std: float) -> np.ndarray:
    """Кадр с ровно заданными mean и std по каждому каналу."""
    frame = np.full((size, size, 3), float(mean), dtype=np.float64)
    frame[:, 0::2, :] = mean - std
    frame[:, 1::2, :] = mean + std
    return np.clip(np.rint(frame), 0, 255).astype(np.uint8)


def _synth_pcm(segments: list[list[float]], sample_rate: int) -> np.ndarray:
    """Моно float32: синус 220 Гц с амплитудой каждого сегмента."""
    chunks = []
    offset = 0
    for duration, amplitude in segments:
        count = int(round(float(duration) * sample_rate))
        if count <= 0:
            continue
        time = (np.arange(offset, offset + count, dtype=np.float64)) / sample_rate
        chunks.append((np.sin(2 * np.pi * 220.0 * time) * float(amplitude)))
        offset += count
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32)


def _slice_segments(
    segments: list[list[float]], start: float, duration: float
) -> list[list[float]]:
    sliced: list[list[float]] = []
    position = 0.0
    end = start + duration
    for length, amplitude in segments:
        seg_start, seg_end = position, position + float(length)
        position = seg_end
        overlap = min(seg_end, end) - max(seg_start, start)
        if overlap > 1e-6:
            sliced.append([round(overlap, 6), float(amplitude)])
    return sliced


# --------------------------------------------------------------------------
# Эмулятор
# --------------------------------------------------------------------------

class FakeFfmpeg:
    """Подмена ``subprocess.run`` для ffmpeg/ffprobe."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd, input=None, capture_output=False, timeout=None, **kwargs):
        args = [str(part) for part in cmd]
        self.calls.append(args)
        tool = Path(args[0]).name.lower()
        if "ffprobe" in tool:
            return self._ffprobe(args)
        return self._ffmpeg(args)

    # -- вспомогательное ---------------------------------------------------

    @staticmethod
    def _done(args: list[str], code: int, stdout: bytes, stderr: bytes):
        return subprocess.CompletedProcess(
            args=args, returncode=code, stdout=stdout, stderr=stderr
        )

    def args_for(self, *needles: str) -> list[list[str]]:
        """Вызовы, в которых встретились все перечисленные подстроки."""
        found = []
        for args in self.calls:
            joined = " ".join(args)
            if all(needle in joined for needle in needles):
                found.append(args)
        return found

    # -- ffprobe -----------------------------------------------------------

    def _ffprobe(self, args: list[str]):
        data = read_media(args[-1])
        if data is None:
            return self._done(
                args,
                1,
                b"",
                b"moov atom not found\nInvalid data found when processing input\n",
            )
        streams = []
        if data["has_video"]:
            streams.append(
                {
                    "codec_type": "video",
                    "codec_name": data["video_codec"],
                    "width": data["width"],
                    "height": data["height"],
                    "r_frame_rate": f"{int(round(float(data['fps']) * 1000))}/1000",
                    "avg_frame_rate": f"{int(round(float(data['fps']) * 1000))}/1000",
                    "nb_frames": str(data["nb_frames"]),
                    "duration": f"{data['duration']:.6f}",
                }
            )
        if data["has_audio"]:
            streams.append(
                {
                    "codec_type": "audio",
                    "codec_name": data["audio_codec"],
                    "sample_rate": str(data["sample_rate"]),
                    "channels": data["channels"],
                    "duration": f"{data['duration']:.6f}",
                }
            )
        payload = {
            "streams": streams,
            "format": {"duration": f"{data['duration']:.6f}"},
        }
        return self._done(args, 0, json.dumps(payload).encode("utf-8"), b"")

    # -- ffmpeg ------------------------------------------------------------

    def _ffmpeg(self, args: list[str]):
        spec = _parse_args(args)
        if spec["output"] == "-" and "rawvideo" in spec["formats"]:
            return self._raw_frame(args, spec)
        if spec["output"] == "-" and "f32le" in spec["formats"]:
            return self._raw_pcm(args, spec)
        if spec["concat"]:
            return self._concat(args, spec)
        return self._transform(args, spec)

    def _raw_frame(self, args: list[str], spec: dict):
        data = read_media(spec["inputs"][0]) if spec["inputs"] else None
        if data is None or not data["has_video"]:
            return self._done(args, 1, b"", b"moov atom not found\n")
        size = (_scale_size(spec["vf"]) or (128, 128))[0]
        mean, std = _level_at(data, spec["ss"] or 0.0)
        return self._done(args, 0, _synth_frame(size, mean, std).tobytes(), b"")

    def _raw_pcm(self, args: list[str], spec: dict):
        data = read_media(spec["inputs"][0]) if spec["inputs"] else None
        if data is None or not data["has_audio"]:
            return self._done(args, 1, b"", b"Invalid data found when processing input\n")
        sample_rate = spec["ar"] or 22050
        segments = data["segments"] or [[data["duration"], 0.2]]
        start = spec["ss"] or 0.0
        duration = spec["t"] if spec["t"] is not None else data["duration"] - start
        samples = _synth_pcm(_slice_segments(segments, start, duration), sample_rate)
        return self._done(args, 0, samples.tobytes(), b"")

    def _concat(self, args: list[str], spec: dict):
        list_path = Path(va.native_path(spec["inputs"][0]))
        try:
            text = list_path.read_text(encoding="utf-8")
        except OSError:
            return self._done(args, 1, b"", b"No such file or directory\n")
        parts: list[str] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            if line != line.lstrip():
                return self._done(args, 1, b"", b"Line with unexpected indentation\n")
            match = re.fullmatch(r"file '(.*)'", line)
            if match is None:
                return self._done(args, 1, b"", b"Unsafe or malformed concat list\n")
            parts.append(match.group(1))
        sources = [read_media(part) for part in parts]
        if not sources or any(src is None for src in sources):
            return self._done(args, 1, b"", b"moov atom not found\n")
        shapes = {
            (src["width"], src["height"], round(float(src["fps"]), 3), src["has_audio"])
            for src in sources
        }
        if len(shapes) > 1:
            # Именно это и ломает concat-демуксер на неприведённых клипах.
            return self._done(
                args, 1, b"", b"Input link parameters do not match the corresponding output\n"
            )
        out = dict(sources[0])
        out["duration"] = round(sum(float(src["duration"]) for src in sources), 6)
        out["nb_frames"] = sum(int(src["nb_frames"]) for src in sources)
        out["segments"] = [seg for src in sources for seg in (src["segments"] or [])]
        levels: list[list[float]] = []
        offset = 0.0
        for src in sources:
            for start, mean, std in src["levels"] or []:
                levels.append([round(offset + float(start), 6), mean, std])
            offset += float(src["duration"])
        out["levels"] = levels
        _dump(spec["output"], out)
        return self._done(args, 0, b"", b"")

    def _transform(self, args: list[str], spec: dict):
        sources = [read_media(item) for item in spec["inputs"]]
        if not sources or any(src is None for src in sources):
            return self._done(args, 1, b"", b"No such file or directory\n")
        video_src = next((src for src in sources if src["has_video"]), None)
        audio_src = next((src for src in sources if src["has_audio"]), None)

        out = dict(DEFAULT_MEDIA)
        base = video_src or audio_src
        duration = float(base["duration"])
        if spec["ss"]:
            duration = max(0.0, duration - spec["ss"])
        if spec["t"] is not None:
            duration = min(duration, float(spec["t"]))
        if spec["shortest"] and video_src and audio_src:
            duration = min(duration, float(audio_src["duration"]))
        out["duration"] = round(duration, 6)

        if video_src is not None and not spec["vn"]:
            size = _scale_size(spec["vf"]) or (video_src["width"], video_src["height"])
            fps = spec["rate"] or _filter_fps(spec["vf"]) or float(video_src["fps"])
            out.update(
                has_video=True,
                width=size[0],
                height=size[1],
                fps=float(fps),
                video_codec="h264",
                nb_frames=int(round(duration * float(fps))),
                levels=[
                    [max(0.0, float(start) - (spec["ss"] or 0.0)), mean, std]
                    for start, mean, std in (video_src["levels"] or [])
                ],
            )
        else:
            out.update(
                has_video=False, width=0, height=0, fps=0.0, nb_frames=0, video_codec=""
            )

        wants_audio = not spec["an"] and (audio_src is not None or spec["lavfi"])
        if wants_audio:
            # Забытые -ar/-ac — та самая деградация: 32 kHz и один канал.
            out.update(
                has_audio=True,
                audio_codec="aac",
                sample_rate=spec["ar"] if spec["ar"] is not None else 32000,
                channels=spec["ac"] if spec["ac"] is not None else 1,
                segments=(
                    _slice_segments(audio_src["segments"], spec["ss"] or 0.0, duration)
                    if audio_src is not None and audio_src["segments"]
                    else [[round(duration, 6), 0.0]]
                ),
            )
        else:
            out.update(has_audio=False, audio_codec="", sample_rate=0, channels=0)

        _dump(spec["output"], out)
        return self._done(args, 0, b"", b"")


@pytest.fixture(autouse=True)
def _clean_registry():
    REGISTRY.clear()
    yield
    REGISTRY.clear()


@pytest.fixture
def ff(monkeypatch) -> FakeFfmpeg:
    """Подменить ffmpeg/ffprobe эмулятором и зафиксировать пути к бинарникам."""
    fake = FakeFfmpeg()
    monkeypatch.setattr(va.subprocess, "run", fake)
    monkeypatch.setattr(va.shutil, "which", lambda name: f"/usr/bin/{name}")
    return fake


# --------------------------------------------------------------------------
# Пути: обратные слэши в ffmpeg
# --------------------------------------------------------------------------

def test_ffmpeg_path_converts_backslashes():
    assert va.ffmpeg_path(r"C:\Users\user\media\clip.mp4") == "C:/Users/user/media/clip.mp4"
    assert va.ffmpeg_path(Path("a") / "b.mp4") == "a/b.mp4"


def test_native_path_uses_os_separator():
    assert va.native_path("a/b/c.mp4") == os.path.join("a", "b", "c.mp4")


def test_probe_sends_forward_slashes_to_ffprobe(tmp_path, ff):
    # Имя с обратными слэшами: на Windows это разделители, на POSIX — часть имени.
    clip = tmp_path / "out\\promo\\clip.mp4"
    write_media(clip)
    va.probe(clip)
    assert ff.calls, "ffprobe не был вызван"
    assert all("\\" not in arg for arg in ff.calls[0])


def test_concat_arguments_never_contain_backslashes(tmp_path, ff):
    first = write_media(tmp_path / "clips\\one.mp4")
    second = write_media(tmp_path / "clips\\two.mp4")
    # workdir задан явно, чтобы список для concat остался на диске для проверки.
    va.concat([first, second], tmp_path / "reel.mp4", workdir=tmp_path / "work")
    for args in ff.calls:
        assert all("\\" not in arg for arg in args), args
    # Список для concat-демуксера тоже содержит только прямые слэши.
    list_args = ff.args_for("-f concat")[0]
    list_path = Path(list_args[list_args.index("-i") + 1])
    assert "\\" not in list_path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# probe / validate
# --------------------------------------------------------------------------

def test_probe_missing_file_raises(tmp_path, ff):
    with pytest.raises(va.MediaError, match="не найден"):
        va.probe(tmp_path / "nope.mp4")


def test_probe_broken_file_raises(tmp_path, ff):
    broken = write_broken(tmp_path / "broken.mp4")
    with pytest.raises(va.MediaError) as excinfo:
        va.probe(broken)
    assert "moov atom not found" in str(excinfo.value)


def test_probe_zero_byte_file_raises(tmp_path, ff):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(va.MediaError, match="пустой"):
        va.probe(empty)


def test_probe_reports_stream_parameters(tmp_path, ff):
    clip = write_media(
        tmp_path / "clip.mp4",
        has_audio=True,
        audio_codec="aac",
        sample_rate=48000,
        channels=2,
        segments=[[5.875, 0.2]],
    )
    info = va.probe(clip)
    assert info["width"] == 768
    assert info["height"] == 922
    assert info["fps"] == pytest.approx(24.0)
    assert info["nb_frames"] == 141
    assert info["duration"] == pytest.approx(5.875)
    assert info["has_audio"] is True
    assert info["sample_rate"] == 48000
    assert info["channels"] == 2


def test_validate_false_on_zero_byte_and_unreadable(tmp_path, ff):
    empty = tmp_path / "zero.mp4"
    empty.write_bytes(b"")
    assert va.validate(empty) is False
    assert va.validate(write_broken(tmp_path / "truncated.mp4")) is False
    assert va.validate(tmp_path / "missing.mp4") is False


def test_validate_true_on_readable_file(tmp_path, ff):
    assert va.validate(write_media(tmp_path / "ok.mp4")) is True


# --------------------------------------------------------------------------
# normalize_clip / concat / rife
# --------------------------------------------------------------------------

def test_normalize_clip_forces_requested_fps(tmp_path, ff):
    src = write_media(tmp_path / "src.mp4", fps=24.0, duration=4.0)
    dst = tmp_path / "norm.mp4"
    result = va.normalize_clip(src, dst, fps=30)
    assert result["fps"] == pytest.approx(30.0)
    assert va.probe(dst)["fps"] == pytest.approx(30.0)
    args = ff.args_for("libx264")[0]
    assert args[args.index("-r") + 1] == "30"
    assert "setsar=1" in args[args.index("-vf") + 1]
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"


def test_normalize_clip_rounds_resolution_to_even(tmp_path, ff):
    src = write_media(tmp_path / "odd.mp4", width=1281, height=801)
    result = va.normalize_clip(src, tmp_path / "even.mp4")
    assert (result["width"], result["height"]) == (1280, 800)


def test_concat_equal_clips_sums_duration(tmp_path, ff):
    first = write_media(tmp_path / "a.mp4", duration=5.875, nb_frames=141)
    second = write_media(tmp_path / "b.mp4", duration=5.875, nb_frames=141)
    out = tmp_path / "reel.mp4"
    result = va.concat([first, second], out)
    assert result["duration"] == pytest.approx(11.75, abs=0.01)
    assert result["nb_frames"] == 282
    assert result["expected_duration"] == pytest.approx(11.75, abs=0.01)
    assert result["seam_times"] == [pytest.approx(5.875)]
    assert (result["width"], result["height"]) == (768, 922)


def test_concat_different_resolutions_scales_and_pads(tmp_path, ff):
    wide = write_media(tmp_path / "wide.mp4", width=1280, height=720, duration=2.0)
    tall = write_media(tmp_path / "tall.mp4", width=768, height=922, duration=3.0)
    out = tmp_path / "mixed.mp4"
    result = va.concat([wide, tall], out)
    # scale+pad в единое разрешение: иначе concat-демуксер падает на aspect error.
    assert (result["width"], result["height"]) == (1280, 922)
    assert result["duration"] == pytest.approx(5.0, abs=0.01)
    filters = [
        args[args.index("-vf") + 1] for args in ff.calls if "-vf" in args
    ]
    assert filters, "клипы не приводились к единым параметрам"
    for value in filters:
        assert "scale=1280:922:force_original_aspect_ratio=decrease" in value
        assert "pad=1280:922" in value


def test_concat_list_file_has_no_indentation(tmp_path):
    text = va._concat_list_text([Path("C:/clips/a.mp4"), Path("C:/clips/b.mp4")])
    lines = text.splitlines()
    assert lines == ["file 'C:/clips/a.mp4'", "file 'C:/clips/b.mp4'"]
    assert all(line == line.lstrip() for line in lines)
    assert text.endswith("\n")


def test_concat_without_clips_raises(tmp_path, ff):
    with pytest.raises(va.MediaError, match="ни одного клипа"):
        va.concat([], tmp_path / "out.mp4")


def test_rife_raises_frame_rate_to_sixty(tmp_path, ff):
    src = write_media(tmp_path / "clip.mp4", fps=24.0, duration=7.2)
    dst = tmp_path / "clip_rife60.mp4"
    result = va.rife(src, dst, fps=60)
    assert result["fps"] == pytest.approx(60.0)
    assert result["nb_frames"] == int(round(7.2 * 60))
    assert va.validate(dst) is True
    args = ff.args_for("minterpolate")[0]
    vf = args[args.index("-vf") + 1]
    assert vf == "minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"


def test_rife_on_broken_source_raises(tmp_path, ff):
    with pytest.raises(va.MediaError):
        va.rife(write_broken(tmp_path / "bad.mp4"), tmp_path / "out.mp4")


# --------------------------------------------------------------------------
# Музыка
# --------------------------------------------------------------------------

def test_trim_music_longer_than_track_raises(tmp_path, ff):
    track = write_audio(tmp_path / "short.mp3", [[3.0, 0.25]])
    with pytest.raises(va.MediaError, match="трек короче"):
        va.trim_music(track, tmp_path / "bed.m4a", 10.0)


def test_trim_music_explicit_start_out_of_range_raises(tmp_path, ff):
    track = write_audio(tmp_path / "track.mp3", [[12.0, 0.25]])
    with pytest.raises(va.MediaError, match="не влезает"):
        va.trim_music(track, tmp_path / "bed.m4a", 6.0, start=9.0)


def test_trim_music_skips_silent_intro(tmp_path, ff):
    # Первые 4 секунды — тихий вход (-51 dB), дальше ровный уровень.
    track = write_audio(tmp_path / "track.mp3", [[4.0, 0.004], [20.0, 0.25]])
    result = va.trim_music(track, tmp_path / "bed.m4a", 5.0)
    assert result["start"] >= 4.0
    assert result["window"]["min_db"] > result["window"]["dip_floor_db"]
    args = ff.args_for("loudnorm")[0]
    assert args[args.index("-ss") + 1] == f"{result['start']:.3f}"


def test_trim_music_normalizes_and_fades(tmp_path, ff):
    track = write_audio(tmp_path / "track.mp3", [[30.0, 0.25]])
    result = va.trim_music(track, tmp_path / "bed.m4a", 11.75, start=2.0, loudness=-14)
    args = ff.args_for("loudnorm")[0]
    chain = args[args.index("-af") + 1]
    assert "loudnorm=I=-14:TP=-1.5:LRA=11" in chain
    assert "afade=t=in:st=0:d=0.6" in chain
    assert "afade=t=out:st=10.75:d=1" in chain
    assert args[args.index("-ar") + 1] == "48000"
    assert args[args.index("-ac") + 1] == "2"
    assert result["duration"] == pytest.approx(11.75, abs=0.01)
    assert result["sample_rate"] == 48000
    assert result["channels"] == 2


def test_choose_music_window_reports_dip_floor(tmp_path, ff):
    track = write_audio(tmp_path / "track.mp3", [[6.0, 0.02], [24.0, 0.3]])
    window = va.choose_music_window(track, 8.0)
    assert window["start"] >= 6.0
    assert window["clean_candidates"] >= 1
    assert window["min_db"] > window["dip_floor_db"]


def test_measure_loudness_uses_rms_bins(tmp_path, ff):
    track = write_audio(tmp_path / "track.m4a", [[4.0, 0.5]])
    stats = va.measure_loudness(track)
    # 0.5 * 1/sqrt(2) ~= 0.354 -> около -9 dB
    assert stats["mean_db"] == pytest.approx(-9.0, abs=0.5)
    assert stats["bins"] == 8


# --------------------------------------------------------------------------
# mux
# --------------------------------------------------------------------------

def test_mux_produces_aac_48k_stereo(tmp_path, ff):
    video = write_media(tmp_path / "video.mp4", duration=11.75)
    audio = write_audio(tmp_path / "bed.m4a", [[11.75, 0.3]])
    out = tmp_path / "final.mp4"
    result = va.mux(video, audio, out)
    assert result["audio_codec"] == "aac"
    assert result["sample_rate"] == 48000
    assert result["channels"] == 2
    args = ff.args_for("+faststart")[0]
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-ar") + 1] == "48000"
    assert args[args.index("-ac") + 1] == "2"


def test_mux_without_audio_stream_raises(tmp_path, ff):
    video = write_media(tmp_path / "video.mp4")
    silent = write_media(tmp_path / "also_video.mp4")
    with pytest.raises(va.MediaError, match="нет аудиопотока"):
        va.mux(video, silent, tmp_path / "final.mp4")


def test_mux_rejects_wrong_sample_rate(tmp_path, ff, monkeypatch):
    """Без -ar/-ac эмулятор отдаёт 32 kHz моно — такой результат не принимаем."""
    monkeypatch.setattr(va, "_audio_encode_args", lambda: ["-c:a", "aac"])
    video = write_media(tmp_path / "video.mp4")
    audio = write_audio(tmp_path / "bed.m4a", [[5.875, 0.3]])
    with pytest.raises(va.MediaError, match="32000 Hz"):
        va.mux(video, audio, tmp_path / "final.mp4")


# --------------------------------------------------------------------------
# Стыки
# --------------------------------------------------------------------------

def test_seam_report_flags_contrast_jump(tmp_path, ff):
    first = write_media(tmp_path / "a.mp4", duration=5.875, levels=[[0.0, 30.0, 6.0]])
    second = write_media(tmp_path / "b.mp4", duration=5.875, levels=[[0.0, 210.0, 40.0]])
    out = tmp_path / "reel.mp4"
    result = va.concat([first, second], out)
    report = va.seam_report(out, result["seam_times"])
    assert len(report) == 1
    entry = report[0]
    assert entry["max_delta_std"] == pytest.approx(34.0, abs=0.5)
    assert entry["visible_seam"] is True


def test_seam_report_clean_video_has_no_flag(tmp_path, ff):
    first = write_media(tmp_path / "a.mp4", duration=4.0, levels=[[0.0, 120.0, 20.0]])
    second = write_media(tmp_path / "b.mp4", duration=4.0, levels=[[0.0, 124.0, 23.0]])
    out = tmp_path / "reel.mp4"
    result = va.concat([first, second], out)
    report = va.seam_report(out, result["seam_times"])
    assert report[0]["max_delta_std"] == pytest.approx(3.0, abs=0.5)
    assert report[0]["visible_seam"] is False


def test_frame_stats_requests_128px_rgb24(tmp_path, ff):
    clip = write_media(tmp_path / "clip.mp4", levels=[[0.0, 100.0, 10.0]])
    stats = va.frame_stats(clip, 1.0)
    assert stats["mean"] == [pytest.approx(100.0)] * 3
    assert stats["std"] == [pytest.approx(10.0)] * 3
    args = ff.args_for("rawvideo")[0]
    assert args[args.index("-vf") + 1] == "scale=128:128"
    assert args[args.index("-pix_fmt") + 1] == "rgb24"
    assert args[-1] == "-"


# --------------------------------------------------------------------------
# Высокоуровневая сборка и CLI
# --------------------------------------------------------------------------

def test_assemble_concats_adds_music_and_checks_seams(tmp_path, ff):
    first = write_media(tmp_path / "a.mp4", duration=5.875, levels=[[0.0, 90.0, 14.0]])
    second = write_media(tmp_path / "b.mp4", duration=5.875, levels=[[0.0, 95.0, 16.0]])
    track = write_audio(tmp_path / "track.mp3", [[40.0, 0.3]])
    out = tmp_path / "final.mp4"
    result = va.assemble([first, second], out, music=track)
    assert result["duration"] == pytest.approx(11.75, abs=0.05)
    assert result["sample_rate"] == 48000
    assert result["channels"] == 2
    assert len(result["seams"]) == 1
    assert result["seams"][0]["visible_seam"] is False


@pytest.mark.parametrize(
    "command",
    ["probe", "validate", "normalize", "concat", "rife", "trim-music", "mux", "seam-report", "assemble"],
)
def test_cli_subcommand_help(command, capsys):
    with pytest.raises(SystemExit) as excinfo:
        va.build_parser().parse_args([command, "--help"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip()


def test_cli_top_level_help(capsys):
    with pytest.raises(SystemExit) as excinfo:
        va.build_parser().parse_args(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for command in ("probe", "concat", "rife", "trim-music", "mux", "seam-report"):
        assert command in out


def test_cli_probe_prints_json(tmp_path, ff, capsys):
    clip = write_media(tmp_path / "clip.mp4")
    assert va.main(["probe", str(clip)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["width"] == 768
    assert payload["nb_frames"] == 141


def test_cli_reports_media_error(tmp_path, ff, capsys):
    assert va.main(["probe", str(tmp_path / "absent.mp4")]) == 1


def test_cli_concat_writes_output(tmp_path, ff, capsys):
    first = write_media(tmp_path / "a.mp4", duration=2.0)
    second = write_media(tmp_path / "b.mp4", duration=3.0)
    out = tmp_path / "reel.mp4"
    assert va.main(["concat", str(first), str(second), "--output", str(out)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["duration"] == pytest.approx(5.0, abs=0.01)


# --------------------------------------------------------------------------
# media_tool: тот же капкан с путями
# --------------------------------------------------------------------------

def test_media_tool_passes_forward_slashes_to_ffmpeg(tmp_path):
    """На Windows ``str(Path)`` даёт обратные слэши и ffmpeg молча пишет пустоту."""
    source = tmp_path / "inbox\\sofa\\big.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"x" * 4096)

    seen: list[list[str]] = []

    def runner(command, **kwargs):
        seen.append(list(command))
        Path(va.native_path(command[-1])).write_bytes(b"y" * 32)
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    prepared = media_tool.ensure_photo_size_under_limit(
        source, limit_bytes=1024, runner=runner
    )
    try:
        assert prepared.resized is True
        assert prepared.final_size == 32
        assert seen, "ffmpeg не был вызван"
        media_args = [arg for arg in seen[0] if arg.endswith((".png", ".jpg"))]
        assert len(media_args) == 2
        assert all("\\" not in arg for arg in media_args)
    finally:
        prepared.cleanup()


def test_media_tool_rejects_empty_ffmpeg_output(tmp_path):
    """ffmpeg вернул 0, но файл пустой — это не результат, а битый вывод."""
    source = tmp_path / "big.png"
    source.write_bytes(b"x" * 4096)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    with pytest.raises(media_tool.ResizeError, match="empty file"):
        media_tool.ensure_photo_size_under_limit(source, limit_bytes=1024, runner=runner)


# --------------------------------------------------------------------------
# Единый формат звука в scene_composer
# --------------------------------------------------------------------------

def test_scene_composer_shares_audio_format(tmp_path, monkeypatch):
    pytest.importorskip("cv2")
    from scripts import scene_composer as sc

    captured: dict = {}

    def fake_run(cmd, input=None, capture_output=False, **kwargs):
        captured["cmd"] = [str(part) for part in cmd]
        Path(va.native_path(cmd[-1])).write_bytes(b"z" * 16)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(sc.subprocess, "run", fake_run)
    monkeypatch.setattr(sc.shutil, "which", lambda name: f"/usr/bin/{name}")

    music = tmp_path / "bed.wav"
    music.write_bytes(b"RIFF")
    composer = sc.SceneComposer(
        tmp_path / "in.png",
        tmp_path / "out.mp4",
        duration_s=1.0,
        fps=2,
        resolution=(8, 8),
        music=music,
    )
    produced = composer._encode([np.zeros((8, 8, 3), dtype=np.uint8)], music)
    try:
        args = captured["cmd"]
        assert args[args.index("-ar") + 1] == str(va.AUDIO_SAMPLE_RATE) == "48000"
        assert args[args.index("-ac") + 1] == str(va.AUDIO_CHANNELS) == "2"
        assert all("\\" not in arg for arg in args)
    finally:
        Path(produced).unlink(missing_ok=True)
