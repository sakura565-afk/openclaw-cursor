#!/usr/bin/env python3
"""Сборка видео: склейка клипов, интерполяция, монтаж звука.

Модуль закрывает то, чего в репозитории не было: `scene_composer.py` умеет
только «PNG + камерное движение + музыка», а полноценного монтажа (склейка
нескольких клипов, подбор музыкального окна под длину ролика, нормализация
громкости, проверка результата) не было нигде.

Использование::

    python scripts/video_assembler.py probe clip.mp4
    python scripts/video_assembler.py concat a.mp4 b.mp4 -o out.mp4
    python scripts/video_assembler.py rife clip.mp4 -o clip_60.mp4
    python scripts/video_assembler.py trim-music track.mp3 -o bed.m4a --duration 11.75
    python scripts/video_assembler.py mux video.mp4 bed.m4a -o final.mp4
    python scripts/video_assembler.py seam-report final.mp4 --seam 7.2

Коды выхода: 0 — успех, 1 — ошибка (MediaError), 2 — неверные аргументы.

Только stdlib + numpy (numpy опционален: без него оконная RMS-огибающая
считается чистым Python).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:  # запуск вида `python scripts/video_assembler.py`
    sys.path.insert(0, str(_REPO_ROOT))

#: Целевой уровень громкости для промо-роликов (streaming-стандарт).
DEFAULT_LOUDNESS_LUFS = -14.0
#: Порог различимой разницы контраста между кадрами на стыке.
#: Ниже — стык не виден глазом, выше — виден.
SEAM_STD_THRESHOLD = 8.0
#: Частота дискретизации и число каналов для AAC-реенкода.
#: Без ``-ar 48000 -ac 2`` сэмплрейт падает до 32 kHz, и звук замедляется
#: с шумом — это проверено на практике.
AUDIO_SAMPLE_RATE = "48000"
AUDIO_CHANNELS = "2"
#: Рецепт RIFE 24 -> 60 fps.
RIFE_FILTER = "minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"


class MediaError(RuntimeError):
    """Ошибка обработки медиафайла (битый файл, отсутствие ffmpeg и т.п.)."""


def _ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG_BIN") or "ffmpeg"


def _ffprobe_bin() -> str:
    return os.environ.get("FFPROBE_BIN") or "ffprobe"


def _ff_path(path: os.PathLike[str] | str) -> str:
    """Путь для ffmpeg — всегда с прямыми слэшами.

    На Windows путь с обратными слэшами в аргументах ffmpeg не находится:
    команда завершается с кодом 0, а файл просто не появляется.
    """
    return str(path).replace("\\", "/")


def _native(path: os.PathLike[str] | str) -> Path:
    """Путь в нативной форме — для проверки существования."""
    return Path(str(path).replace("/", os.sep))


@dataclass(frozen=True)
class MediaInfo:
    """Что ffprobe знает о файле."""

    path: str
    width: int
    height: int
    fps: float
    nb_frames: int
    duration: float
    has_audio: bool
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    codec: str = ""

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height if self.height else 0.0


def _parse_fps(value: str) -> float:
    """ffprobe отдаёт fps как дробь ``30000/1001``."""
    if not value:
        return 0.0
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            return float(num) / float(den) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def probe(path: os.PathLike[str] | str) -> MediaInfo:
    """Прочитать метаданные файла. Бросает MediaError, если файл нечитаем."""
    native = _native(path)
    if not native.exists():
        raise MediaError(f"Файл не найден: {native}")
    cmd = [
        _ffprobe_bin(), "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,nb_frames,codec_name",
        "-show_entries", "format=duration",
        "-of", "json",
        _ff_path(native),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
    except FileNotFoundError as exc:
        raise MediaError(f"ffprobe не найден: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"ffprobe не ответил за 120 с: {native}") from exc
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise MediaError(f"ffprobe не смог прочитать {native.name}: {stderr[:200]}")
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise MediaError(f"ffprobe вернул не-JSON для {native.name}: {exc}") from exc

    streams = data.get("streams") or []
    if not streams:
        raise MediaError(f"В {native.name} нет видеопотока")
    vstream = streams[0]
    fmt = data.get("format") or {}

    audio = _probe_audio(native)

    return MediaInfo(
        path=str(native),
        width=int(vstream.get("width") or 0),
        height=int(vstream.get("height") or 0),
        fps=_parse_fps(str(vstream.get("r_frame_rate") or "")),
        nb_frames=int(vstream.get("nb_frames") or 0),
        duration=float(fmt.get("duration") or 0.0),
        has_audio=audio is not None,
        sample_rate=audio[0] if audio else None,
        channels=audio[1] if audio else None,
        codec=str(vstream.get("codec_name") or ""),
    )


def _probe_audio(path: Path) -> Optional[tuple[int, int]]:
    cmd = [
        _ffprobe_bin(), "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels",
        "-of", "json", _ff_path(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        streams = (json.loads(proc.stdout.decode("utf-8", "replace")).get("streams") or [])
    except json.JSONDecodeError:
        return None
    if not streams:
        return None
    st = streams[0]
    return int(st.get("sample_rate") or 0), int(st.get("channels") or 0)


def validate(path: os.PathLike[str] | str, *, kind: str = "video") -> bool:
    """True, если файл существует, непуст и читается ffprobe.

    Проверять только существование нельзя: ffmpeg пишет атом ``moov`` в
    конце файла, поэтому прерванный прогон оставляет непустой, но
    нечитаемый mp4 (``moov atom not found``).

    ``kind="audio"`` проверяет наличие аудиопотока, ``kind="video"`` —
    видеопотока. У аудиофайла видеопотока нет, поэтому единая проверка
    давала бы ложное «нечитаемо».
    """
    native = _native(path)
    if not native.exists():
        return False
    try:
        if native.stat().st_size <= 0:
            return False
    except OSError:
        return False
    try:
        if kind == "audio":
            cmd = [
                _ffprobe_bin(), "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=codec_name", "-of", "json", _ff_path(native),
            ]
            proc = subprocess.run(cmd, capture_output=True, timeout=60)
            if proc.returncode != 0:
                return False
            streams = json.loads(proc.stdout.decode("utf-8", "replace")).get("streams") or []
            return bool(streams)
        probe(native)
    except (MediaError, json.JSONDecodeError, OSError):
        return False
    return True


def _run(cmd: Sequence[str], timeout: int = 2400) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise MediaError(f"Исполняемый файл не найден: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"ffmpeg не ответил за {timeout} с") from exc


def normalize_clip(
    src: os.PathLike[str] | str,
    dst: os.PathLike[str] | str,
    *,
    fps: int = 24,
    crf: int = 18,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Path:
    """Привести клип к единым параметрам перед склейкой.

    Concat-демuxer не терпит разные time base и разные разрешения, поэтому
    все клипы приводятся к общему виду. Если заданы width/height, клип
    масштабируется с сохранением AR и дополняется полями (scale+pad) —
    иначе возникает aspect error.
    """
    source = _native(src)
    target = _native(dst)
    if not source.exists():
        raise MediaError(f"Файл не найден: {source}")

    if width and height:
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={fps}"
        )
    else:
        vf = f"fps={fps},setsar=1"

    cmd = [
        _ffmpeg_bin(), "-y", "-v", "error",
        "-i", _ff_path(source),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-r", str(fps), "-an",
        _ff_path(target),
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg не смог нормализовать клип: {proc.stderr[-400:]}")
    if not validate(target):
        raise MediaError(f"После нормализации файл нечитаем: {target.name}")
    return target


def rife(src: os.PathLike[str] | str, dst: os.PathLike[str] | str, *, fps: int = 60) -> Path:
    """Интерполяция до заданной частоты кадров (RIFE)."""
    source = _native(src)
    target = _native(dst)
    if not source.exists():
        raise MediaError(f"Файл не найден: {source}")
    vf = RIFE_FILTER if fps == 60 else f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc"
    cmd = [
        _ffmpeg_bin(), "-y", "-v", "error",
        "-i", _ff_path(source),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-ar", AUDIO_SAMPLE_RATE, "-ac", AUDIO_CHANNELS,
        _ff_path(target),
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg не смог интерполировать: {proc.stderr[-400:]}")
    if not validate(target):
        raise MediaError(f"После интерполяции файл нечитаем: {target.name}")
    return target


def concat(
    clips: Sequence[os.PathLike[str] | str],
    dst: os.PathLike[str] | str,
    *,
    fps: int = 24,
    crf: int = 18,
    workdir: Optional[os.PathLike[str] | str] = None,
) -> Path:
    """Жёсткая склейка клипов.

    Клипы с разными разрешениями приводятся к общему (scale+pad), иначе
    concat либо разъезжается по aspect, либо даёт чёрные поля.
    """
    if not clips:
        raise MediaError("Нечего склеивать: список клипов пуст")
    target = _native(dst)
    tmp_dir = _native(workdir) if workdir else target.parent
    tmp_dir.mkdir(parents=True, exist_ok=True)

    infos = [probe(c) for c in clips]
    width = max(i.width for i in infos)
    height = max(i.height for i in infos)

    prepared: list[Path] = []
    for idx, clip in enumerate(clips):
        out = tmp_dir / f"_concat_{idx:02d}.mp4"
        normalize_clip(clip, out, fps=fps, crf=crf, width=width, height=height)
        prepared.append(out)

    lst = tmp_dir / "_concat_list.txt"
    # Список пишется без отступов: ffmpeg воспринимает отступ как часть пути.
    lst.write_text(
        "".join(f"file '{_ff_path(p)}'\n" for p in prepared), encoding="utf-8", newline="\n"
    )

    cmd = [
        _ffmpeg_bin(), "-y", "-v", "error",
        "-f", "concat", "-safe", "0", "-i", _ff_path(lst),
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-r", str(fps),
        _ff_path(target),
    ]
    proc = _run(cmd)
    for p in prepared:
        try:
            p.unlink()
        except OSError:
            pass
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg не смог склеить: {proc.stderr[-400:]}")
    if not validate(target):
        raise MediaError(f"После склейки файл нечитаем: {target.name}")
    return target


def mux(
    video: os.PathLike[str] | str,
    audio: os.PathLike[str] | str,
    dst: os.PathLike[str] | str,
    *,
    bitrate: str = "192k",
) -> Path:
    """Свести видео и звук в mp4."""
    vsrc, asrc, target = _native(video), _native(audio), _native(dst)
    for p in (vsrc, asrc):
        if not p.exists():
            raise MediaError(f"Файл не найден: {p}")
    cmd = [
        _ffmpeg_bin(), "-y", "-v", "error",
        "-i", _ff_path(vsrc), "-i", _ff_path(asrc),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", bitrate,
        "-ar", AUDIO_SAMPLE_RATE, "-ac", AUDIO_CHANNELS,
        "-shortest", "-movflags", "+faststart",
        _ff_path(target),
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg не смог свести звук: {proc.stderr[-400:]}")
    if not validate(target):
        raise MediaError(f"После сведения файл нечитаем: {target.name}")
    return target


# --------------------------------------------------------------------------
# Работа со звуком
# --------------------------------------------------------------------------


def _decode_pcm(src: Path, rate: int = 44100) -> list[float]:
    """Декодировать аудио в моно float32 (чистый Python, без numpy)."""
    cmd = [
        _ffmpeg_bin(), "-v", "error", "-i", _ff_path(src),
        "-ac", "1", "-ar", str(rate), "-f", "s16le", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=600)
    if proc.returncode != 0 or not proc.stdout:
        raise MediaError(f"ffmpeg не смог декодировать звук: {src.name}")
    raw = proc.stdout
    count = len(raw) // 2
    import array

    samples = array.array("h")
    samples.frombytes(raw[: count * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return [s / 32768.0 for s in samples]


def _rms_envelope(samples: Sequence[float], rate: int, bucket: float = 0.5) -> list[float]:
    """RMS по корзинам заданной длины (в dB)."""
    size = max(1, int(rate * bucket))
    out: list[float] = []
    for i in range(0, len(samples) - size + 1, size):
        chunk = samples[i: i + size]
        if not chunk:
            break
        acc = 0.0
        for v in chunk:
            acc += v * v
        rms = math.sqrt(acc / len(chunk))
        out.append(20.0 * math.log10(rms + 1e-9))
    return out


def pick_music_window(
    samples: Sequence[float],
    rate: int,
    duration: float,
    *,
    bucket: float = 0.5,
    floor_db: float = -45.0,
) -> float:
    """Выбрать лучшее окно длиной ``duration`` в треке.

    Критерий: окно не должно начинаться в тишине и не должно содержать
    провалов громкости. Окна с минимальным средним уровнем внутри и
    максимальным «запасом» над порогом тишины выигрывают.
    """
    env = _rms_envelope(samples, rate, bucket)
    need = int(math.ceil(duration / bucket))
    if need <= 0 or len(env) < need:
        raise MediaError(
            f"Трек короче требуемого окна: {len(env) * bucket:.1f} с < {duration:.1f} с"
        )
    best: Optional[tuple[float, float]] = None
    for i in range(0, len(env) - need + 1):
        window = env[i: i + need]
        if window[0] < floor_db:
            continue  # вход из тишины
        if min(window) < floor_db:
            continue  # провал внутри окна
        mean = sum(window) / len(window)
        # предпочитаем громкое и ровное окно
        score = mean - 0.5 * (max(window) - min(window))
        if best is None or score > best[0]:
            best = (score, i * bucket)
    if best is None:
        raise MediaError("Не найдено пригодного окна в треке")
    return float(best[1])


def trim_music(
    music: os.PathLike[str] | str,
    dst: os.PathLike[str] | str,
    duration: float,
    *,
    start: Optional[float] = None,
    loudness: float = DEFAULT_LOUDNESS_LUFS,
    fade_in: float = 0.6,
    fade_out: float = 1.0,
) -> Path:
    """Вырезать фрагмент трека под длину ролика и нормализовать громкость."""
    source = _native(music)
    target = _native(dst)
    if not source.exists():
        raise MediaError(f"Файл не найден: {source}")

    if start is None:
        samples = _decode_pcm(source)
        start = pick_music_window(samples, 44100, duration)
    else:
        start = float(start)

    total = _music_duration(source)
    if start + duration > total + 0.5:
        raise MediaError(
            f"Окно выходит за пределы трека: {start:.1f} + {duration:.1f} > {total:.1f} с"
        )

    filters = [f"loudnorm=I={loudness}:TP=-1.5:LRA=11"]
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        filters.append(f"afade=t=out:st={max(0.0, duration - fade_out):.2f}:d={fade_out}")

    cmd = [
        _ffmpeg_bin(), "-y", "-v", "error",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
        "-i", _ff_path(source),
        "-af", ",".join(filters),
        "-c:a", "aac", "-b:a", "192k",
        "-ar", AUDIO_SAMPLE_RATE, "-ac", AUDIO_CHANNELS,
        _ff_path(target),
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg не смог вырезать музыку: {proc.stderr[-400:]}")
    if not validate(target, kind="audio"):
        raise MediaError(f"После обработки звук нечитаем: {target.name}")
    return target


def _music_duration(path: Path) -> float:
    cmd = [
        _ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", _ff_path(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=60)
    try:
        return float(proc.stdout.decode("utf-8", "replace").strip())
    except ValueError as exc:
        raise MediaError(f"Не удалось узнать длительность трека: {path.name}") from exc


# --------------------------------------------------------------------------
# Проверка стыка
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SeamSample:
    """Замер кадра по каналам: среднее и стандартное отклонение."""

    time: float
    means: tuple[float, ...]
    stds: tuple[float, ...]


@dataclass(frozen=True)
class SeamReport:
    """Результат проверки стыка."""

    seam_time: float
    before: SeamSample
    at: SeamSample
    after: SeamSample
    mean_delta: float
    std_delta: float
    visible: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "seam_time": self.seam_time,
            "mean_delta": round(self.mean_delta, 2),
            "std_delta": round(self.std_delta, 2),
            "visible": self.visible,
            "before": {"means": list(self.before.means), "stds": list(self.before.stds)},
            "at": {"means": list(self.at.means), "stds": list(self.at.stds)},
            "after": {"means": list(self.after.means), "stds": list(self.after.stds)},
        }


def frame_stats(path: os.PathLike[str] | str, t: float, size: int = 128) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Среднее и std по каждому каналу RGB кадра в момент ``t``."""
    cmd = [
        _ffmpeg_bin(), "-v", "error", "-ss", f"{t:.3f}",
        "-i", _ff_path(_native(path)),
        "-frames:v", "1", "-vf", f"scale={size}:{size}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=60)
    px = proc.stdout
    if len(px) < size * size * 3:
        raise MediaError(f"Не удалось получить кадр на t={t:.2f} с")
    means: list[float] = []
    stds: list[float] = []
    for ch in range(3):
        values = px[ch::3]
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / len(values)
        means.append(mean)
        stds.append(math.sqrt(var))
    return tuple(means), tuple(stds)


def seam_report(
    video: os.PathLike[str] | str,
    seam_time: float,
    *,
    window: float = 0.35,
    threshold: float = SEAM_STD_THRESHOLD,
) -> SeamReport:
    """Проверить стык по контрасту, а не по среднему RGB.

    Среднее по каналу — одна скалярная величина: она не описывает контраст.
    Два кадра с одинаковым mean могут отличаться радикально по контрасту, и
    глаз это читает мгновенно. Поэтому сравниваются и mean, и std; разница
    std больше ``threshold`` означает видимый стык.
    """
    before_m, before_s = frame_stats(video, max(0.0, seam_time - window))
    at_m, at_s = frame_stats(video, seam_time)
    after_m, after_s = frame_stats(video, seam_time + window)

    mean_delta = sum(abs(a - b) for a, b in zip(at_m, before_m)) / len(at_m)
    std_delta = sum(abs(a - b) for a, b in zip(at_s, before_s)) / len(at_s)
    return SeamReport(
        seam_time=seam_time,
        before=SeamSample(seam_time - window, before_m, before_s),
        at=SeamSample(seam_time, at_m, at_s),
        after=SeamSample(seam_time + window, after_m, after_s),
        mean_delta=mean_delta,
        std_delta=std_delta,
        visible=std_delta > threshold,
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _cmd_probe(args: argparse.Namespace) -> int:
    info = probe(args.file)
    print(json.dumps({
        "path": info.path,
        "width": info.width,
        "height": info.height,
        "aspect_ratio": round(info.aspect_ratio, 4),
        "fps": info.fps,
        "nb_frames": info.nb_frames,
        "duration": round(info.duration, 3),
        "has_audio": info.has_audio,
        "sample_rate": info.sample_rate,
        "channels": info.channels,
        "codec": info.codec,
        "valid": True,
    }, ensure_ascii=False, indent=2))
    return 0


def _cmd_concat(args: argparse.Namespace) -> int:
    out = concat(args.clips, args.output, fps=args.fps, workdir=args.workdir)
    print(json.dumps({"output": str(out), "valid": validate(out)}, ensure_ascii=False))
    return 0


def _cmd_rife(args: argparse.Namespace) -> int:
    out = rife(args.file, args.output, fps=args.fps)
    print(json.dumps({"output": str(out), "valid": validate(out)}, ensure_ascii=False))
    return 0


def _cmd_trim_music(args: argparse.Namespace) -> int:
    out = trim_music(
        args.file, args.output, args.duration,
        start=args.start, loudness=args.loudness,
    )
    print(json.dumps({"output": str(out), "start": args.start}, ensure_ascii=False))
    return 0


def _cmd_mux(args: argparse.Namespace) -> int:
    out = mux(args.video, args.audio, args.output)
    print(json.dumps({"output": str(out), "valid": validate(out)}, ensure_ascii=False))
    return 0


def _cmd_seam_report(args: argparse.Namespace) -> int:
    report = seam_report(args.file, args.seam, threshold=args.threshold)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Сборка видео: склейка клипов, интерполяция, монтаж звука."
    )
    sub = parser.add_subparsers(dest="command")

    p_probe = sub.add_parser("probe", help="метаданные файла")
    p_probe.add_argument("file")
    p_probe.set_defaults(func=_cmd_probe)

    p_concat = sub.add_parser("concat", help="жёсткая склейка клипов")
    p_concat.add_argument("clips", nargs="+")
    p_concat.add_argument("-o", "--output", required=True)
    p_concat.add_argument("--fps", type=int, default=24)
    p_concat.add_argument("--workdir", default=None)
    p_concat.set_defaults(func=_cmd_concat)

    p_rife = sub.add_parser("rife", help="интерполяция до 60 fps")
    p_rife.add_argument("file")
    p_rife.add_argument("-o", "--output", required=True)
    p_rife.add_argument("--fps", type=int, default=60)
    p_rife.set_defaults(func=_cmd_rife)

    p_trim = sub.add_parser("trim-music", help="вырезать и нормализовать музыку")
    p_trim.add_argument("file")
    p_trim.add_argument("-o", "--output", required=True)
    p_trim.add_argument("--duration", type=float, required=True)
    p_trim.add_argument("--start", type=float, default=None)
    p_trim.add_argument("--loudness", type=float, default=DEFAULT_LOUDNESS_LUFS)
    p_trim.set_defaults(func=_cmd_trim_music)

    p_mux = sub.add_parser("mux", help="свести видео и звук")
    p_mux.add_argument("video")
    p_mux.add_argument("audio")
    p_mux.add_argument("-o", "--output", required=True)
    p_mux.set_defaults(func=_cmd_mux)

    p_seam = sub.add_parser("seam-report", help="проверить стык по контрасту")
    p_seam.add_argument("file")
    p_seam.add_argument("--seam", type=float, required=True)
    p_seam.add_argument("--threshold", type=float, default=SEAM_STD_THRESHOLD)
    p_seam.set_defaults(func=_cmd_seam_report)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    try:
        return int(args.func(args))
    except MediaError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Прервано", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
