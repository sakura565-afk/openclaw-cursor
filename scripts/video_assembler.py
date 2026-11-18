#!/usr/bin/env python3
"""Монтажный слой для промо-роликов: склейка, музыка, микс, контроль стыков.

``scene_composer.py`` делает отдельные клипы (PNG -> MP4 с движением камеры),
``media_tool.py`` готовит картинки под Telegram. Этот модуль закрывает то, что
между ними: привести клипы к единым параметрам, склеить, подобрать фрагмент
музыки под длину ролика, нормализовать громкость, собрать итоговый mp4 и
проверить результат.

Правила, выстраданные на реальных прогонах (ломать их нельзя):

1. **Пути в ffmpeg — только с прямыми слэшами.** На Windows ``str(Path)`` даёт
   ``C:\\Users\\...``; ffmpeg такой аргумент не находит, при этом нередко
   завершается с кодом 0, и файл просто не появляется на диске. Поэтому каждый
   путь уходит в ffmpeg через :func:`ffmpeg_path`, а существование проверяется
   в нативной форме через :func:`native_path`.
2. **AAC-реенкод всегда с ``-ar 48000 -ac 2``.** Без явных флагов сэмплрейт
   уезжает (наблюдали падение до 32 kHz), звук замедляется и шумит, а число
   каналов не гарантировано.
3. **После каждого вызова ffmpeg — ``ffprobe``, а не ``os.path.exists``.**
   Атом ``moov`` пишется в КОНЦЕ файла: прерванный процесс оставляет непустой,
   но нечитаемый mp4 (``moov atom not found``).
4. **Склейка — жёсткий concat, не ``xfade``.** ``xfade`` даёт двоение фигуры и
   скачок яркости в кадре перехода. Для concat-демуксера все клипы обязаны
   иметь одинаковые разрешение, SAR и fps.
5. **Стык проверяется по контрасту (std), а не по среднему RGB.** Два кадра с
   одинаковым mean могут отличаться по контрасту радикально.

Примеры::

    python video_assembler.py probe clip.mp4
    python video_assembler.py concat a.mp4 b.mp4 --output reel.mp4
    python video_assembler.py trim-music track.mp3 bed.m4a --duration 11.75
    python video_assembler.py mux reel.mp4 bed.m4a final.mp4
    python video_assembler.py seam-report reel.mp4 --at 5.875
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

try:
    from scripts.windows_console import decode_console_output
except ImportError:  # pragma: no cover - прямой запуск скрипта
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from windows_console import decode_console_output

LOGGER = logging.getLogger("video_assembler")

# -- единый формат вывода ---------------------------------------------------
# 48 kHz stereo: проверенный рабочий пайплайн. Без явных -ar/-ac ffmpeg
# опускает сэмплрейт на AAC-реенкоде и не гарантирует два канала.
AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 2
AUDIO_BITRATE = "192k"

DEFAULT_FPS = 24
DEFAULT_CRF = 18
RIFE_FPS = 60
RIFE_CRF = 20
X264_PRESET = "medium"
# Общий знаменатель time base: concat-демуксер не терпит разные таймскейлы.
VIDEO_TIMESCALE = 90000

# Громкость промо-ролика.
TARGET_LOUDNESS_LUFS = -14.0
LOUDNORM_TRUE_PEAK = -1.5
LOUDNORM_LRA = 11
MUSIC_FADE_IN_S = 0.6
MUSIC_FADE_OUT_S = 1.0

# Анализ музыки: RMS-огибающая корзинами по 0.5 с + спектральный поток.
ENVELOPE_BIN_S = 0.5
ANALYSIS_SAMPLE_RATE = 22050
FLUX_WINDOW = 1024
FLUX_HOP = 512
# Провал = корзина тише общего уровня трека на столько дБ. Абсолютные значения
# наблюдённых провалов -31..-37 dB при общем уровне около -20 dB.
DIP_MARGIN_DB = 6.0
ONSET_SCORE_WEIGHT = 6.0

# Контроль стыка: кадр 128x128, разница std по каналу > 8 единиц — видно глазом.
SEAM_PROBE_SIZE = 128
SEAM_STD_THRESHOLD = 8.0
SEAM_OFFSET_S = 0.12

_EPS = 1e-9


class MediaError(RuntimeError):
    """Медиафайл отсутствует, нечитаем, или параметры операции невозможны."""


# --------------------------------------------------------------------------
# Пути и запуск внешних утилит
# --------------------------------------------------------------------------

def ffmpeg_path(path: os.PathLike[str] | str) -> str:
    """Вернуть путь в форме, которую понимает ffmpeg (только ``/``).

    Обратные слэши ffmpeg трактует как escape-последовательности: вход/выход не
    находится, а код возврата при этом может остаться нулевым. Преобразование
    безусловное — Windows принимает ``/`` в путях наравне с ``\\``.
    """
    return str(path).replace("\\", "/")


def native_path(path: os.PathLike[str] | str) -> str:
    """Вернуть путь в нативной форме ОС — для проверок существования.

    После :func:`ffmpeg_path` путь содержит ``/``; на Windows ``os.path.exists``
    такой путь переварит, но ``os.sep``-форма честнее и переносима.
    """
    text = str(path)
    if os.sep != "/":
        text = text.replace("/", os.sep)
    return text


def _decode(raw: bytes | str | None) -> str:
    """Декодировать вывод ffmpeg: сначала UTF-8, потом кодовые страницы Windows."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return decode_console_output(raw)


def tool_binary(name: str) -> str:
    """Найти ffmpeg/ffprobe в PATH, иначе — у ``imageio-ffmpeg``."""
    exe = shutil.which(name)
    if exe:
        return exe
    if name == "ffmpeg":
        try:  # опциональная зависимость, только как источник бинарника
            import imageio_ffmpeg  # type: ignore

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:  # pragma: no cover - зависит от окружения
            pass
    # Отдаём имя как есть: отсутствие бинарника превратится в MediaError при вызове.
    return name


def _run(
    cmd: Sequence[str],
    *,
    payload: bytes | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Запустить ffmpeg/ffprobe, не давая ошибкам окружения утечь наружу."""
    LOGGER.debug("CMD: %s", " ".join(str(part) for part in cmd))
    try:
        return subprocess.run(
            list(cmd),
            input=payload,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise MediaError(f"Не найден исполняемый файл: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        # Прерванный ffmpeg оставляет mp4 без атома moov — файл есть, но битый.
        raise MediaError(
            f"{cmd[0]} не успел за {timeout} с; результат считать битым"
        ) from exc


# --------------------------------------------------------------------------
# probe / validate
# --------------------------------------------------------------------------

def _parse_rate(value: Any) -> float:
    """Разобрать ffprobe-дробь вида ``24/1`` в число кадров в секунду."""
    if value in (None, "", "0/0"):
        return 0.0
    text = str(value)
    if "/" in text:
        num, _, den = text.partition("/")
        try:
            numerator = float(num)
            denominator = float(den)
        except ValueError:
            return 0.0
        if abs(denominator) < _EPS:
            return 0.0
        return numerator / denominator
    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def probe(path: os.PathLike[str] | str) -> dict:
    """Прочитать параметры медиафайла через ``ffprobe``.

    Возвращает словарь с width/height/fps/nb_frames/duration/has_audio/
    sample_rate/channels. Бросает :class:`MediaError`, если файла нет, он пустой
    или нечитаем (``moov atom not found`` у прерванного ffmpeg).
    """
    target = native_path(path)
    if not os.path.exists(target):
        raise MediaError(f"Файл не найден: {target}")
    size = os.path.getsize(target)
    if size == 0:
        raise MediaError(f"Файл пустой (0 байт): {target}")

    cmd = [
        tool_binary("ffprobe"),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        ffmpeg_path(path),
    ]
    proc = _run(cmd)
    stderr = _decode(proc.stderr).strip()
    if proc.returncode != 0:
        raise MediaError(f"ffprobe не смог прочитать {target}: {stderr or 'нет вывода'}")
    # ffprobe иногда отдаёт 0 на обрезанном файле, но ругается в stderr.
    lowered = stderr.lower()
    if "moov atom not found" in lowered or "invalid data found" in lowered:
        raise MediaError(f"Файл нечитаем ({stderr}): {target}")

    try:
        data = json.loads(_decode(proc.stdout) or "{}")
    except json.JSONDecodeError as exc:
        raise MediaError(f"Не разобрать вывод ffprobe для {target}: {exc}") from exc

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise MediaError(f"В файле нет ни видео-, ни аудиопотока: {target}")

    fmt = data.get("format") or {}
    duration = _parse_float(fmt.get("duration"))
    if duration <= 0 and video is not None:
        duration = _parse_float(video.get("duration"))
    if duration <= 0 and audio is not None:
        duration = _parse_float(audio.get("duration"))

    fps = 0.0
    width = height = 0
    nb_frames = 0
    video_codec = ""
    if video is not None:
        fps = _parse_rate(video.get("r_frame_rate")) or _parse_rate(
            video.get("avg_frame_rate")
        )
        width = _parse_int(video.get("width"))
        height = _parse_int(video.get("height"))
        nb_frames = _parse_int(video.get("nb_frames"))
        video_codec = str(video.get("codec_name") or "")
        if nb_frames <= 0 and fps > 0 and duration > 0:
            nb_frames = int(round(duration * fps))

    return {
        "path": str(path),
        "size_bytes": size,
        "width": width,
        "height": height,
        "fps": round(fps, 6),
        "nb_frames": nb_frames,
        "duration": round(duration, 6),
        "video_codec": video_codec,
        "has_video": video is not None,
        "has_audio": audio is not None,
        "audio_codec": str((audio or {}).get("codec_name") or ""),
        "sample_rate": _parse_int((audio or {}).get("sample_rate")),
        "channels": _parse_int((audio or {}).get("channels")),
    }


def validate(path: os.PathLike[str] | str) -> bool:
    """Файл есть, он не пустой и ``ffprobe`` его читает.

    Вызывать после КАЖДОЙ операции: прерванный ffmpeg оставляет непустой, но
    нечитаемый mp4, который ``os.path.exists`` считает успехом.
    """
    try:
        probe(path)
    except MediaError as exc:
        LOGGER.debug("validate(%s) -> False: %s", path, exc)
        return False
    return True


def _require_valid(path: os.PathLike[str] | str, operation: str) -> dict:
    """Проверить результат операции через ffprobe и вернуть его параметры."""
    try:
        return probe(path)
    except MediaError as exc:
        raise MediaError(f"{operation}: результат не прошёл проверку — {exc}") from exc


def _ffmpeg_or_fail(cmd: Sequence[str], operation: str, *, timeout: float | None = None) -> None:
    proc = _run(cmd, timeout=timeout)
    if proc.returncode != 0:
        raise MediaError(f"{operation}: ffmpeg вернул {proc.returncode}\n{_decode(proc.stderr).strip()}")


# --------------------------------------------------------------------------
# Приведение клипов и склейка
# --------------------------------------------------------------------------

def _even(value: float) -> int:
    """Ближайшее не большее чётное число: yuv420p требует чётных сторон."""
    number = int(value)
    return max(2, number - (number % 2))


def _scale_pad_filter(width: int, height: int, fps: int | None = None) -> str:
    """Фильтр «впишись и добей чёрным»: без растяжения и без aspect error."""
    parts = [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
        "setsar=1",
    ]
    if fps:
        parts.append(f"fps={fps}")
    return ",".join(parts)


def _audio_encode_args() -> list[str]:
    """AAC 48 kHz stereo — единый формат звука (см. правило 2 в докстринге)."""
    return [
        "-c:a",
        "aac",
        "-b:a",
        AUDIO_BITRATE,
        "-ar",
        str(AUDIO_SAMPLE_RATE),
        "-ac",
        str(AUDIO_CHANNELS),
    ]


def normalize_clip(
    src: os.PathLike[str] | str,
    dst: os.PathLike[str] | str,
    *,
    fps: int = DEFAULT_FPS,
    crf: int = DEFAULT_CRF,
    resolution: tuple[int, int] | None = None,
    ensure_audio: bool = False,
) -> dict:
    """Привести клип к единым параметрам перед склейкой.

    Разрешение, fps, SAR, пиксельный формат и таймскейл выравниваются, иначе
    concat-демуксер даёт чёрные поля, растяжение или рассыпающийся time base.
    """
    info = probe(src)
    if fps <= 0:
        raise MediaError(f"normalize_clip: fps должен быть > 0, получено {fps}")
    if not info["has_video"]:
        raise MediaError(f"normalize_clip: в файле нет видеопотока: {native_path(src)}")
    if resolution is not None:
        if min(resolution) <= 0:
            raise MediaError(f"normalize_clip: некорректное разрешение {resolution}")
        width, height = _even(resolution[0]), _even(resolution[1])
    else:
        if info["width"] <= 0 or info["height"] <= 0:
            raise MediaError(f"normalize_clip: не определить разрешение источника {native_path(src)}")
        width, height = _even(info["width"]), _even(info["height"])

    Path(native_path(dst)).parent.mkdir(parents=True, exist_ok=True)

    cmd = [tool_binary("ffmpeg"), "-y", "-v", "error", "-i", ffmpeg_path(src)]
    add_silence = ensure_audio and not info["has_audio"]
    if add_silence:
        cmd += [
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_SAMPLE_RATE}",
        ]
    cmd += [
        "-map",
        "0:v:0",
    ]
    if add_silence:
        cmd += ["-map", "1:a:0"]
    elif info["has_audio"]:
        cmd += ["-map", "0:a:0"]
    cmd += [
        "-vf",
        _scale_pad_filter(width, height, fps),
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        X264_PRESET,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-video_track_timescale",
        str(VIDEO_TIMESCALE),
    ]
    if info["has_audio"] or add_silence:
        cmd += _audio_encode_args()
    else:
        cmd += ["-an"]
    if add_silence:
        cmd += ["-shortest"]
    cmd += [ffmpeg_path(dst)]

    _ffmpeg_or_fail(cmd, "normalize_clip")
    return _require_valid(dst, "normalize_clip")


def rife(
    src: os.PathLike[str] | str,
    dst: os.PathLike[str] | str,
    *,
    fps: int = RIFE_FPS,
    crf: int = RIFE_CRF,
    timeout: float | None = None,
) -> dict:
    """Интерполировать клип до ``fps`` (проверенный рецепт minterpolate).

    ``mi_mode=mci`` + ``mc_mode=aobmc`` + ``me_mode=bidir`` + ``vsbmc=1`` —
    тот самый набор, который на практике даёт чистые промежуточные кадры.
    Считается небыстро: клип 7.2 с с 24 на 60 fps — около минуты на RTX 3060.
    """
    if fps <= 0:
        raise MediaError(f"rife: fps должен быть > 0, получено {fps}")
    info = probe(src)
    if not info["has_video"]:
        raise MediaError(f"rife: в файле нет видеопотока: {src}")

    Path(native_path(dst)).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        tool_binary("ffmpeg"),
        "-y",
        "-v",
        "error",
        "-i",
        ffmpeg_path(src),
        "-vf",
        (
            f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:"
            "me_mode=bidir:vsbmc=1"
        ),
        "-c:v",
        "libx264",
        "-preset",
        X264_PRESET,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-video_track_timescale",
        str(VIDEO_TIMESCALE),
    ]
    if info["has_audio"]:
        cmd += _audio_encode_args()
    else:
        cmd += ["-an"]
    cmd += [ffmpeg_path(dst)]

    _ffmpeg_or_fail(cmd, "rife", timeout=timeout)
    return _require_valid(dst, "rife")


def _concat_list_text(paths: Iterable[os.PathLike[str] | str]) -> str:
    """Собрать текст списка для concat-демуксера: без отступов, по строке на файл."""
    lines = []
    for item in paths:
        # Внутри одинарных кавычек сам апостроф закрывается и добавляется отдельно.
        escaped = ffmpeg_path(item).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def concat(
    clips: Sequence[os.PathLike[str] | str],
    dst: os.PathLike[str] | str,
    *,
    fps: int | None = None,
    crf: int = DEFAULT_CRF,
    resolution: tuple[int, int] | None = None,
    workdir: os.PathLike[str] | str | None = None,
) -> dict:
    """Склеить клипы жёстким concat (без ``xfade``) и проверить результат.

    ``xfade`` на мебели даёт фантомное двоение фигуры и скачок яркости в кадре
    перехода, поэтому переходов нет вообще. Все клипы предварительно приводятся
    к одному разрешению (scale+pad, без растяжения), fps и таймскейлу.
    """
    clip_list = [Path(native_path(clip)) for clip in clips]
    if not clip_list:
        raise MediaError("concat: не передано ни одного клипа")
    infos = [probe(clip) for clip in clip_list]

    target_fps = int(fps) if fps else max(1, int(round(max(i["fps"] for i in infos) or DEFAULT_FPS)))
    if resolution is not None:
        target_w, target_h = _even(resolution[0]), _even(resolution[1])
    else:
        target_w = _even(max(i["width"] for i in infos))
        target_h = _even(max(i["height"] for i in infos))
    needs_audio = any(i["has_audio"] for i in infos)

    temp_root = Path(native_path(workdir)) if workdir else Path(tempfile.mkdtemp(prefix="concat-"))
    temp_root.mkdir(parents=True, exist_ok=True)
    owns_temp = workdir is None
    try:
        normalized: list[Path] = []
        staged_durations: list[float] = []
        for index, clip in enumerate(clip_list):
            staged = temp_root / f"part_{index:03d}.mp4"
            staged_info = normalize_clip(
                clip,
                staged,
                fps=target_fps,
                crf=crf,
                resolution=(target_w, target_h),
                ensure_audio=needs_audio,
            )
            normalized.append(staged)
            staged_durations.append(float(staged_info["duration"]))

        list_file = temp_root / "concat_list.txt"
        list_file.write_text(_concat_list_text(normalized), encoding="utf-8", newline="\n")

        Path(native_path(dst)).parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            tool_binary("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            ffmpeg_path(list_file),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            ffmpeg_path(dst),
        ]
        _ffmpeg_or_fail(cmd, "concat")
        result = _require_valid(dst, "concat")
    finally:
        if owns_temp:
            shutil.rmtree(temp_root, ignore_errors=True)

    # Стыки считаем по приведённым клипам: после выравнивания fps длительности
    # могут чуть сдвинуться, и момент перехода вместе с ними.
    expected = sum(staged_durations)
    result["expected_duration"] = round(expected, 6)
    result["seam_times"] = [round(value, 6) for value in _seam_times(staged_durations)]
    if expected > 0 and abs(result["duration"] - expected) > max(0.5, expected * 0.05):
        LOGGER.warning(
            "concat: длительность результата %.3f с заметно расходится с суммой %.3f с",
            result["duration"],
            expected,
        )
    return result


def _seam_times(durations: Sequence[float]) -> list[float]:
    """Моменты стыков — накопленные длительности, кроме финального."""
    times: list[float] = []
    running = 0.0
    for value in durations[:-1]:
        running += float(value)
        times.append(running)
    return times


# --------------------------------------------------------------------------
# Музыка: огибающая, онсеты, выбор окна, нормализация
# --------------------------------------------------------------------------

def decode_audio(
    path: os.PathLike[str] | str,
    *,
    sample_rate: int = ANALYSIS_SAMPLE_RATE,
) -> np.ndarray:
    """Декодировать звук в моно float32 для анализа в numpy."""
    cmd = [
        tool_binary("ffmpeg"),
        "-v",
        "error",
        "-i",
        ffmpeg_path(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "-",
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise MediaError(
            f"Не удалось декодировать звук {native_path(path)}: {_decode(proc.stderr).strip()}"
        )
    raw = proc.stdout or b""
    usable = len(raw) - (len(raw) % 4)
    samples = np.frombuffer(raw[:usable], dtype="<f4")
    if samples.size == 0:
        raise MediaError(f"В файле нет аудиосэмплов: {native_path(path)}")
    return np.asarray(samples, dtype=np.float32)


def _rms_db(block: np.ndarray) -> float:
    """RMS в дБ по 0.5-секундной корзине (ebur128 на коротком фрагменте врёт)."""
    if block.size == 0:
        return -120.0
    value = float(np.sqrt(np.mean(np.square(block.astype(np.float64)))))
    return 20.0 * math.log10(max(value, 1e-12))


def _spectral_flux(samples: np.ndarray, sample_rate: int, bins: int, bin_s: float) -> list[float]:
    """Спектральный поток (онсеты), усреднённый по тем же корзинам, что и RMS."""
    flux_per_bin = [0.0] * bins
    if samples.size < FLUX_WINDOW + FLUX_HOP:
        return flux_per_bin

    frame_count = 1 + (samples.size - FLUX_WINDOW) // FLUX_HOP
    window = np.hanning(FLUX_WINDOW).astype(np.float32)
    totals = [0.0] * bins
    counts = [0] * bins
    previous: np.ndarray | None = None
    # Считаем блоками, чтобы не держать весь спектр длинного трека в памяти.
    block_frames = 256
    for block_start in range(0, frame_count, block_frames):
        block_end = min(frame_count, block_start + block_frames)
        offsets = np.arange(block_start, block_end) * FLUX_HOP
        index = offsets[:, None] + np.arange(FLUX_WINDOW)[None, :]
        frames = samples[index] * window[None, :]
        magnitude = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)
        for row in range(magnitude.shape[0]):
            current = magnitude[row]
            if previous is not None:
                value = float(np.sum(np.maximum(current - previous, 0.0)))
                frame_index = block_start + row
                center = (frame_index * FLUX_HOP + FLUX_WINDOW / 2.0) / sample_rate
                bin_index = min(bins - 1, int(center / bin_s))
                totals[bin_index] += value
                counts[bin_index] += 1
            previous = current
    for i in range(bins):
        if counts[i]:
            flux_per_bin[i] = totals[i] / counts[i]
    return flux_per_bin


def music_envelope(
    path: os.PathLike[str] | str,
    *,
    bin_s: float = ENVELOPE_BIN_S,
    sample_rate: int = ANALYSIS_SAMPLE_RATE,
) -> dict:
    """RMS-огибающая и онсеты трека корзинами по ``bin_s`` секунд."""
    if bin_s <= 0:
        raise MediaError(f"music_envelope: bin_s должен быть > 0, получено {bin_s}")
    samples = decode_audio(path, sample_rate=sample_rate)
    bin_samples = max(1, int(round(bin_s * sample_rate)))
    bins = max(1, samples.size // bin_samples)
    rms = [
        _rms_db(samples[i * bin_samples : (i + 1) * bin_samples]) for i in range(bins)
    ]
    return {
        "bin_s": bin_s,
        "sample_rate": sample_rate,
        "duration": samples.size / float(sample_rate),
        "rms_db": rms,
        "track_rms_db": _rms_db(samples),
        "flux": _spectral_flux(samples, sample_rate, bins, bin_s),
    }


def choose_music_window(
    path: os.PathLike[str] | str,
    duration: float,
    *,
    bin_s: float = ENVELOPE_BIN_S,
    envelope: dict | None = None,
) -> dict:
    """Выбрать окно длиной ``duration``: без провалов и с ровным ритмом.

    Окна, в которых есть корзина тише общего уровня трека на ``DIP_MARGIN_DB``,
    отбрасываются целиком — так отсекается тихий вход трека (наблюдали -28 dB
    на первых 12 секундах) и дыры в середине. Среди оставшихся выигрывает самое
    ровное и плотное по уровню, с бонусом за сильные доли.
    """
    if duration <= 0:
        raise MediaError(f"choose_music_window: duration должен быть > 0, получено {duration}")
    data = envelope or music_envelope(path, bin_s=bin_s)
    rms: list[float] = list(data["rms_db"])
    flux: list[float] = list(data.get("flux") or [0.0] * len(rms))
    bin_s = float(data.get("bin_s", bin_s))
    track_duration = float(data["duration"])
    if duration > track_duration + 0.05:
        raise MediaError(
            f"Трек короче запрошенного окна: {track_duration:.2f} с < {duration:.2f} с"
        )

    span = max(1, int(math.ceil(duration / bin_s)))
    if span > len(rms):
        span = len(rms)
    dip_floor = float(data["track_rms_db"]) - DIP_MARGIN_DB
    flux_peak = max(flux) if flux else 0.0

    candidates: list[dict] = []
    for start_bin in range(0, len(rms) - span + 1):
        chunk = rms[start_bin : start_bin + span]
        start_s = start_bin * bin_s
        if start_s + duration > track_duration + 0.05:
            break
        chunk_flux = flux[start_bin : start_bin + span] or [0.0]
        mean_db = sum(chunk) / len(chunk)
        min_db = min(chunk)
        has_dip = min_db < dip_floor
        flux_norm = (sum(chunk_flux) / len(chunk_flux) / flux_peak) if flux_peak > 0 else 0.0
        onset_offset = chunk_flux.index(max(chunk_flux)) * bin_s
        candidates.append(
            {
                "start": round(start_s, 6),
                "mean_db": round(mean_db, 2),
                "min_db": round(min_db, 2),
                "has_dip": has_dip,
                "flux_norm": round(flux_norm, 4),
                "onset_t": round(start_s + onset_offset, 3),
                "score": round(mean_db + min_db + ONSET_SCORE_WEIGHT * flux_norm, 4),
            }
        )
    if not candidates:
        raise MediaError(
            f"Не из чего выбирать окно {duration:.2f} с в треке {track_duration:.2f} с"
        )

    clean = [c for c in candidates if not c["has_dip"]]
    pool = clean or candidates
    if not clean:
        LOGGER.warning(
            "choose_music_window: все окна содержат провалы ниже %.1f dB, берём наименее плохое",
            dip_floor,
        )
    best = max(pool, key=lambda c: (c["score"], -c["start"]))
    best = dict(best)
    best["dip_floor_db"] = round(dip_floor, 2)
    best["track_rms_db"] = round(float(data["track_rms_db"]), 2)
    best["candidates"] = len(candidates)
    best["clean_candidates"] = len(clean)
    return best


def trim_music(
    music: os.PathLike[str] | str,
    dst: os.PathLike[str] | str,
    duration: float,
    *,
    start: float | None = None,
    loudness: float = TARGET_LOUDNESS_LUFS,
    fade_in: float = MUSIC_FADE_IN_S,
    fade_out: float = MUSIC_FADE_OUT_S,
) -> dict:
    """Вырезать музыкальный фрагмент под длину ролика и нормализовать громкость.

    Если ``start`` не задан — окно выбирается по огибающей (см.
    :func:`choose_music_window`). Уровень приводится к ``loudness`` LUFS,
    добавляются fade-in и fade-out, звук пишется в AAC 48 kHz stereo.
    """
    duration = float(duration)
    if duration <= 0:
        raise MediaError(f"trim_music: duration должен быть > 0, получено {duration}")
    info = probe(music)
    if not info["has_audio"]:
        raise MediaError(f"trim_music: в файле нет аудиопотока: {native_path(music)}")
    track_duration = float(info["duration"])
    if track_duration > 0 and duration > track_duration + 0.05:
        raise MediaError(
            f"trim_music: трек короче ролика — {track_duration:.2f} с < {duration:.2f} с"
        )

    window: dict | None = None
    if start is None:
        window = choose_music_window(music, duration)
        start_s = float(window["start"])
    else:
        start_s = float(start)
        if start_s < 0:
            raise MediaError(f"trim_music: start не может быть отрицательным ({start_s})")
        if track_duration > 0 and start_s + duration > track_duration + 0.05:
            raise MediaError(
                f"trim_music: окно {start_s:.2f}..{start_s + duration:.2f} с "
                f"не влезает в трек длиной {track_duration:.2f} с"
            )

    fade_in = max(0.0, min(float(fade_in), duration / 2.0))
    fade_out = max(0.0, min(float(fade_out), duration / 2.0))
    filters = [
        f"loudnorm=I={loudness:g}:TP={LOUDNORM_TRUE_PEAK:g}:LRA={LOUDNORM_LRA:g}",
    ]
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={fade_in:g}")
    if fade_out > 0:
        filters.append(f"afade=t=out:st={max(0.0, duration - fade_out):g}:d={fade_out:g}")

    Path(native_path(dst)).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        tool_binary("ffmpeg"),
        "-y",
        "-v",
        "error",
        "-ss",
        f"{start_s:.3f}",
        "-i",
        ffmpeg_path(music),
        "-t",
        f"{duration:.3f}",
        "-vn",
        "-af",
        ",".join(filters),
        *_audio_encode_args(),
        ffmpeg_path(dst),
    ]
    _ffmpeg_or_fail(cmd, "trim_music")
    result = _require_valid(dst, "trim_music")
    result["start"] = round(start_s, 3)
    result["requested_duration"] = round(duration, 3)
    result["loudness_target"] = loudness
    result["fade_in"] = fade_in
    result["fade_out"] = fade_out
    if window is not None:
        result["window"] = {
            key: window[key]
            for key in ("mean_db", "min_db", "onset_t", "dip_floor_db", "track_rms_db")
            if key in window
        }
    return result


def measure_loudness(path: os.PathLike[str] | str, *, bin_s: float = ENVELOPE_BIN_S) -> dict:
    """Померить уровень фрагмента по RMS-корзинам.

    ``ebur128`` корректен на полном треке, но на коротком фрагменте выдаёт
    бессмысленные -70 LUFS, поэтому контроль результата — через RMS в Python.
    """
    data = music_envelope(path, bin_s=bin_s)
    rms = data["rms_db"]
    return {
        "duration": round(data["duration"], 3),
        "mean_db": round(float(data["track_rms_db"]), 2),
        "min_bin_db": round(min(rms), 2),
        "max_bin_db": round(max(rms), 2),
        "bins": len(rms),
    }


# --------------------------------------------------------------------------
# Сборка итогового файла
# --------------------------------------------------------------------------

def mux(
    video: os.PathLike[str] | str,
    audio: os.PathLike[str] | str,
    dst: os.PathLike[str] | str,
) -> dict:
    """Собрать итоговый mp4: видео копией, звук в AAC 48 kHz stereo, faststart."""
    video_info = probe(video)
    audio_info = probe(audio)
    if not video_info["has_video"]:
        raise MediaError(f"mux: в файле нет видеопотока: {native_path(video)}")
    if not audio_info["has_audio"]:
        raise MediaError(f"mux: в файле нет аудиопотока: {native_path(audio)}")

    Path(native_path(dst)).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        tool_binary("ffmpeg"),
        "-y",
        "-v",
        "error",
        "-i",
        ffmpeg_path(video),
        "-i",
        ffmpeg_path(audio),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        *_audio_encode_args(),
        "-movflags",
        "+faststart",
        "-shortest",
        ffmpeg_path(dst),
    ]
    _ffmpeg_or_fail(cmd, "mux")
    result = _require_valid(dst, "mux")
    if result["sample_rate"] and result["sample_rate"] != AUDIO_SAMPLE_RATE:
        raise MediaError(
            f"mux: на выходе {result['sample_rate']} Hz вместо {AUDIO_SAMPLE_RATE} — "
            "звук поедет по скорости"
        )
    if result["channels"] and result["channels"] != AUDIO_CHANNELS:
        raise MediaError(
            f"mux: на выходе {result['channels']} канал(ов) вместо {AUDIO_CHANNELS}"
        )
    return result


# --------------------------------------------------------------------------
# Контроль стыков
# --------------------------------------------------------------------------

def frame_stats(
    video: os.PathLike[str] | str,
    at: float,
    *,
    size: int = SEAM_PROBE_SIZE,
) -> dict:
    """(mean, std) по каналам RGB на кадре в момент ``at``, уменьшенном до ``size``."""
    cmd = [
        tool_binary("ffmpeg"),
        "-v",
        "error",
        "-ss",
        f"{max(0.0, float(at)):.3f}",
        "-i",
        ffmpeg_path(video),
        "-frames:v",
        "1",
        "-vf",
        f"scale={size}:{size}",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    proc = _run(cmd)
    expected = size * size * 3
    raw = proc.stdout or b""
    if proc.returncode != 0 or len(raw) < expected:
        raise MediaError(
            f"Не удалось снять кадр на {at:.3f} с из {native_path(video)}: "
            f"{_decode(proc.stderr).strip() or f'получено {len(raw)} из {expected} байт'}"
        )
    pixels = np.frombuffer(raw[:expected], dtype=np.uint8).reshape(size, size, 3).astype(np.float64)
    return {
        "t": round(float(at), 3),
        "mean": [round(float(v), 3) for v in pixels.mean(axis=(0, 1))],
        "std": [round(float(v), 3) for v in pixels.std(axis=(0, 1))],
    }


def seam_report(
    video: os.PathLike[str] | str,
    seam_times: Sequence[float],
    *,
    offset: float = SEAM_OFFSET_S,
    threshold: float = SEAM_STD_THRESHOLD,
    size: int = SEAM_PROBE_SIZE,
) -> list[dict]:
    """Оценить заметность стыков по контрасту кадров до/на/после перехода.

    Среднее по каналу контраст не описывает: два кадра с одинаковым mean могут
    отличаться радикально, и глаз это читает мгновенно. Поэтому решает разница
    std — больше ``threshold`` единиц считаем видимым стыком.
    """
    info = probe(video)
    duration = float(info["duration"])
    last = max(0.0, duration - 0.05) if duration > 0 else 0.0

    report: list[dict] = []
    for seam in seam_times:
        seam_t = float(seam)
        before_t = max(0.0, seam_t - offset)
        after_t = min(last, seam_t + offset) if last > 0 else seam_t + offset
        at_t = min(last, seam_t) if last > 0 else seam_t

        before = frame_stats(video, before_t, size=size)
        at = frame_stats(video, at_t, size=size)
        after = frame_stats(video, after_t, size=size)

        delta_std = [round(a - b, 3) for a, b in zip(after["std"], before["std"])]
        delta_mean = [round(a - b, 3) for a, b in zip(after["mean"], before["mean"])]
        max_delta_std = max((abs(v) for v in delta_std), default=0.0)
        report.append(
            {
                "seam_t": round(seam_t, 3),
                "before": before,
                "at": at,
                "after": after,
                "delta_std": delta_std,
                "delta_mean": delta_mean,
                "max_delta_std": round(max_delta_std, 3),
                "threshold": threshold,
                "visible_seam": bool(max_delta_std > threshold),
            }
        )
    return report


# --------------------------------------------------------------------------
# Высокоуровневая сборка
# --------------------------------------------------------------------------

def assemble(
    clips: Sequence[os.PathLike[str] | str],
    dst: os.PathLike[str] | str,
    *,
    music: os.PathLike[str] | str | None = None,
    fps: int | None = None,
    crf: int = DEFAULT_CRF,
    music_start: float | None = None,
    loudness: float = TARGET_LOUDNESS_LUFS,
) -> dict:
    """Склеить клипы, подложить музыку под длину ролика и проверить стыки."""
    workdir = Path(tempfile.mkdtemp(prefix="assemble-"))
    try:
        silent = workdir / "video.mp4"
        video_info = concat(clips, silent, fps=fps, crf=crf)
        seams = video_info.get("seam_times") or []
        if music is None:
            shutil.copyfile(native_path(silent), native_path(dst))
            result = _require_valid(dst, "assemble")
        else:
            bed = workdir / "music.m4a"
            music_info = trim_music(
                music,
                bed,
                video_info["duration"],
                start=music_start,
                loudness=loudness,
            )
            result = mux(silent, bed, dst)
            result["music"] = music_info
        result["seam_times"] = seams
        result["seams"] = seam_report(dst, seams) if seams else []
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video_assembler.py",
        description=(
            "Монтажный слой для промо-роликов: склейка клипов, подбор "
            "музыкального фрагмента, микс и проверка стыков."
        ),
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Подробный лог.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe_parser = subparsers.add_parser("probe", help="Параметры медиафайла (ffprobe).")
    probe_parser.add_argument("path", help="Путь к медиафайлу.")

    validate_parser = subparsers.add_parser(
        "validate", help="Проверить, что файл есть, не пустой и читается."
    )
    validate_parser.add_argument("path", help="Путь к медиафайлу.")

    normalize_parser = subparsers.add_parser(
        "normalize", help="Привести клип к единым параметрам перед склейкой."
    )
    normalize_parser.add_argument("src", help="Исходный клип.")
    normalize_parser.add_argument("dst", help="Куда записать результат.")
    normalize_parser.add_argument("--fps", type=int, default=DEFAULT_FPS, help="Частота кадров.")
    normalize_parser.add_argument("--crf", type=int, default=DEFAULT_CRF, help="CRF для libx264.")
    normalize_parser.add_argument(
        "--resolution",
        type=int,
        nargs=2,
        metavar=("WIDTH", "HEIGHT"),
        default=None,
        help="Целевое разрешение (по умолчанию — как у источника).",
    )

    concat_parser = subparsers.add_parser("concat", help="Жёстко склеить клипы в один mp4.")
    concat_parser.add_argument("clips", nargs="+", help="Клипы в порядке склейки.")
    concat_parser.add_argument("--output", "-o", required=True, help="Итоговый mp4.")
    concat_parser.add_argument("--fps", type=int, default=None, help="Частота кадров результата.")
    concat_parser.add_argument("--crf", type=int, default=DEFAULT_CRF, help="CRF для libx264.")
    concat_parser.add_argument(
        "--resolution",
        type=int,
        nargs=2,
        metavar=("WIDTH", "HEIGHT"),
        default=None,
        help="Целевое разрешение (по умолчанию — максимум по клипам).",
    )

    rife_parser = subparsers.add_parser("rife", help="Интерполяция кадров (minterpolate).")
    rife_parser.add_argument("src", help="Исходный клип.")
    rife_parser.add_argument("dst", help="Куда записать результат.")
    rife_parser.add_argument("--fps", type=int, default=RIFE_FPS, help="Целевая частота кадров.")
    rife_parser.add_argument("--crf", type=int, default=RIFE_CRF, help="CRF для libx264.")

    music_parser = subparsers.add_parser(
        "trim-music", help="Вырезать фрагмент музыки под длину ролика."
    )
    music_parser.add_argument("music", help="Исходный трек.")
    music_parser.add_argument("dst", help="Куда записать фрагмент.")
    music_parser.add_argument(
        "--duration", type=float, required=True, help="Длина фрагмента в секундах."
    )
    music_parser.add_argument(
        "--start",
        type=float,
        default=None,
        help="Начало окна; без него окно выбирается по огибающей.",
    )
    music_parser.add_argument(
        "--loudness",
        type=float,
        default=TARGET_LOUDNESS_LUFS,
        help=f"Целевой уровень в LUFS (по умолчанию {TARGET_LOUDNESS_LUFS:g}).",
    )

    mux_parser = subparsers.add_parser("mux", help="Собрать mp4 из видео и звука.")
    mux_parser.add_argument("video", help="Видеодорожка (копируется без реенкода).")
    mux_parser.add_argument("audio", help="Звуковая дорожка.")
    mux_parser.add_argument("dst", help="Итоговый mp4.")

    seam_parser = subparsers.add_parser("seam-report", help="Проверить заметность стыков.")
    seam_parser.add_argument("video", help="Склеенный ролик.")
    seam_parser.add_argument(
        "--at",
        type=float,
        nargs="+",
        required=True,
        metavar="SECONDS",
        help="Моменты стыков в секундах.",
    )
    seam_parser.add_argument(
        "--threshold",
        type=float,
        default=SEAM_STD_THRESHOLD,
        help=f"Порог разницы std (по умолчанию {SEAM_STD_THRESHOLD:g}).",
    )

    assemble_parser = subparsers.add_parser(
        "assemble", help="Склеить клипы, подложить музыку и проверить стыки."
    )
    assemble_parser.add_argument("clips", nargs="+", help="Клипы в порядке склейки.")
    assemble_parser.add_argument("--output", "-o", required=True, help="Итоговый mp4.")
    assemble_parser.add_argument("--music", default=None, help="Музыкальный трек.")
    assemble_parser.add_argument("--fps", type=int, default=None, help="Частота кадров результата.")
    assemble_parser.add_argument("--crf", type=int, default=DEFAULT_CRF, help="CRF для libx264.")
    assemble_parser.add_argument(
        "--start", type=float, default=None, help="Начало музыкального окна."
    )
    assemble_parser.add_argument(
        "--loudness", type=float, default=TARGET_LOUDNESS_LUFS, help="Целевой уровень в LUFS."
    )
    return parser


def _emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    try:
        if args.command == "probe":
            _emit(probe(args.path))
        elif args.command == "validate":
            ok = validate(args.path)
            _emit({"path": args.path, "valid": ok})
            return 0 if ok else 1
        elif args.command == "normalize":
            resolution = tuple(args.resolution) if args.resolution else None
            _emit(normalize_clip(args.src, args.dst, fps=args.fps, crf=args.crf, resolution=resolution))
        elif args.command == "concat":
            resolution = tuple(args.resolution) if args.resolution else None
            _emit(concat(args.clips, args.output, fps=args.fps, crf=args.crf, resolution=resolution))
        elif args.command == "rife":
            _emit(rife(args.src, args.dst, fps=args.fps, crf=args.crf))
        elif args.command == "trim-music":
            _emit(
                trim_music(
                    args.music,
                    args.dst,
                    args.duration,
                    start=args.start,
                    loudness=args.loudness,
                )
            )
        elif args.command == "mux":
            _emit(mux(args.video, args.audio, args.dst))
        elif args.command == "seam-report":
            _emit(seam_report(args.video, args.at, threshold=args.threshold))
        elif args.command == "assemble":
            _emit(
                assemble(
                    args.clips,
                    args.output,
                    music=args.music,
                    fps=args.fps,
                    crf=args.crf,
                    music_start=args.start,
                    loudness=args.loudness,
                )
            )
        else:  # pragma: no cover - argparse не пустит сюда
            raise MediaError(f"Неизвестная подкоманда: {args.command}")
    except MediaError as exc:
        LOGGER.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
