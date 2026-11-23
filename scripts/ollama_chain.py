#!/usr/bin/env python3
"""Цепочка fallback-моделей Ollama для автоматизации OpenClaw.

Этот модуль — единственный источник правды о том, какие модели входят в
цепочку, сколько каждая из них имеет времени на ответ и как её вызывать
(локальный/облачный ``ollama run`` или xAI по HTTP). Раньше рабочая версия
жила вне репозитория (в ``workspace/scripts``), и клон репозитория получал
мёртвую цепочку: ``402 Payment Required`` на платных моделях и ``retired``
на снятых с публикации. Теперь логика целиком здесь, хостовая копия
обновляется из репозитория, а не наоборот.

Публичный API: :data:`DEFAULT_CHAIN`, :func:`run_model`, :func:`chain_models`,
:func:`call_chain`, :func:`load_chain_yaml`, :func:`_call_ollama`,
:func:`_call_xai`, :func:`is_cloud_model`, :func:`model_timeout`.

Зависимости — только стандартная библиотека. PyYAML используется, когда он
установлен, иначе YAML-конфиг разбирается встроенным мини-парсером.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from functools import partial
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

try:
    from scripts.windows_console import decode_console_output
except ImportError:  # pragma: no cover - прямой запуск файла, без пакета
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from windows_console import decode_console_output

REPO_ROOT = Path(__file__).resolve().parents[1]

# Ollama называет облачные модели и через ``:cloud`` (``gpt-oss:20b:cloud``),
# и через ``-cloud`` в теге (``gemma4:31b-cloud``). Оба варианта — облако.
CLOUD_SUFFIXES: tuple[str, ...] = (":cloud", "-cloud")

# Статусы пробы одной модели. ``not_installed`` и ``skipped`` — не ошибки:
# первый означает «локальной модели нет на диске», второй — «нет ключа/плана».
STATUS_OK = "ok"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"
STATUS_NOT_INSTALLED = "not_installed"
STATUS_SKIPPED = "skipped"
PROBE_STATUSES: tuple[str, ...] = (
    STATUS_OK,
    STATUS_TIMEOUT,
    STATUS_ERROR,
    STATUS_NOT_INSTALLED,
    STATUS_SKIPPED,
)

# Облако отвечает за секунды, локальные модели на RTX 3060 тратят ещё
# десятки секунд на cold start (загрузку весов в VRAM), поэтому таймауты
# разные. Жёсткие 15 c, которые стояли раньше, локальные модели не успевали.
CLOUD_TIMEOUT_SECONDS = 25.0
LOCAL_TIMEOUT_SECONDS = 120.0
DEFAULT_TIMEOUT_SECONDS = 30.0

# Порядок: сначала дешёвое облако (быстро, не занимает VRAM), затем локальные
# модели как offline-запасной вариант. Каждая модель проверена живым прогоном
# ``ollama run`` на хосте (ollama 0.40.7) — «нет в ``ollama list``» не значит
# «не работает»: облачные модели Ollama тянет по требованию.
DEFAULT_CHAIN: tuple[str, ...] = (
    # облачная, проверено 21.10.2026: ответ ~1.2 c, 67 tok/s; в ollama list
    # отсутствует, тянется по требованию; плановый таймаут 25 c
    "gpt-oss:20b:cloud",
    # облачная, проверено 21.10.2026: есть в ollama list; таймаут 25 c.
    # Сам Ollama рекомендует её взамен снятых qwen3-coder-next/devstral-small-2
    "gemma4:31b-cloud",
    # облачная, проверено 21.10.2026: есть в ollama list; таймаут 25 c
    "nemotron-3-nano:30b-cloud",
    # локальная, проверено 21.10.2026: отвечает; таймаут 90 c (cold start)
    "lfm2.5-thinking:latest",
    # локальная 9.6 GB, проверено 21.10.2026: на 15 c не успевала, на 30-40 c
    # отвечает; плановый таймаут 120 c
    "gemma4:e4b",
    # локальная 1.5b, проверено 21.10.2026: отвечает быстро; таймаут 60 c
    "deepcoder:1.5b",
)

# Персональный таймаут модели; всё, чего здесь нет, получает
# ``DEFAULT_TIMEOUT_SECONDS``.
MODEL_TIMEOUTS: dict[str, float] = {
    "gpt-oss:20b:cloud": CLOUD_TIMEOUT_SECONDS,
    "gemma4:31b-cloud": CLOUD_TIMEOUT_SECONDS,
    "nemotron-3-nano:30b-cloud": CLOUD_TIMEOUT_SECONDS,
    "lfm2.5-thinking:latest": 90.0,
    "gemma4:e4b": LOCAL_TIMEOUT_SECONDS,
    "deepcoder:1.5b": 60.0,
}

# xAI идёт не через ollama, а через HTTP API и требует ключ.
XAI_API_URL = "https://api.x.ai/v1/chat/completions"
XAI_MODEL_PREFIX = "grok-"

# Подписи провайдера, по которым модель считается недоступной «по политике»,
# а не сломанной: платный план (402) и снятые с публикации модели (retired).
UNAVAILABLE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b402\b|payment required|usage credits|not included in your free usage",
            re.IGNORECASE,
        ),
        "paid plan required (402 / usage credits)",
    ),
    (re.compile(r"\bretired\b", re.IGNORECASE), "model retired by the provider"),
)

DEFAULT_CHAIN_CONFIG = REPO_ROOT / "config" / "ollama_chain.yaml"
CHAIN_CONFIG_ENV = "OLLAMA_CHAIN_CONFIG"


def is_cloud_model(name: str) -> bool:
    """Облачная ли модель — по суффиксу ``:cloud`` / ``-cloud`` в имени."""
    model = (name or "").strip().lower()
    return any(model.endswith(suffix) for suffix in CLOUD_SUFFIXES)


def is_xai_model(name: str) -> bool:
    """Модель семейства Grok (xAI), доступная только по HTTP с ключом."""
    return (name or "").strip().lower().startswith(XAI_MODEL_PREFIX)


def model_timeout(model: str, timeouts: Mapping[str, float] | None = None) -> float:
    """Таймаут модели в секундах: из словаря, иначе общий дефолт."""
    table = MODEL_TIMEOUTS if timeouts is None else timeouts
    value = table.get(model)
    if value is None:
        value = table.get((model or "").strip().lower())
    if value is None:
        return DEFAULT_TIMEOUT_SECONDS
    return float(value)


def provider_unavailable_reason(text: str | None) -> str | None:
    """Причина, по которой провайдер сам отказал (платный план / retired)."""
    message = text or ""
    if not message:
        return None
    for pattern, reason in UNAVAILABLE_PATTERNS:
        if pattern.search(message):
            return reason
    return None


def chain_models(models: Sequence[str] | None = None) -> tuple[str, ...]:
    """Вернуть цепочку для обхода (по умолчанию — :data:`DEFAULT_CHAIN`)."""
    if models is None:
        return DEFAULT_CHAIN
    return tuple(models)


def run_model(
    model: str,
    prompt: str,
    *,
    timeout: float | None = None,
    popen: type = subprocess.Popen,
) -> tuple[int, str, str]:
    """Прогнать один промпт через ``ollama run`` по stdin/stdout.

    Возвращает ``(returncode, stdout, stderr)``. Поднимает
    ``subprocess.TimeoutExpired``, когда истекает *timeout*; ``timeout=None``
    означает персональный таймаут модели из :func:`model_timeout`.

    Сигнатура зафиксирована: на неё завязан ``scripts/ollama_daily_smoke.py``.
    """
    effective_timeout = model_timeout(model) if timeout is None else timeout
    proc = popen(
        ["ollama", "run", model],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # Консоль Windows отдаёт cp866/cp1251, а не UTF-8: без errors="replace"
        # прогон падал бы с UnicodeDecodeError вместо ответа модели.
        errors="replace",
    )
    try:
        stdout, stderr = proc.communicate(
            input=prompt if prompt.endswith("\n") else prompt + "\n",
            timeout=effective_timeout,
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        raise
    return int(proc.returncode or 0), stdout or "", stderr or ""


def list_installed_models(
    *,
    run_command: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    timeout: float = 20.0,
) -> tuple[str, ...]:
    """Имена моделей из ``ollama list`` (пустой кортеж, если список пуст).

    Исключения (``ollama`` не установлен, демон не отвечает) наружу не
    гасятся: вызывающий сам решает, считать ли это «список неизвестен».
    """
    completed = run_command(
        ["ollama", "list"],
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    stdout = decode_console_output(getattr(completed, "stdout", "") or "")
    models: list[str] = []
    for index, line in enumerate(stdout.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        name = stripped.split()[0]
        if index == 0 and name.upper() in {"NAME", "NAME:"}:
            continue
        models.append(name)
    return tuple(models)


def is_model_installed(model: str, installed: Iterable[str] | None) -> bool:
    """Есть ли локальная модель на диске.

    ``installed=None`` означает «список неизвестен» — тогда считаем, что
    модель есть, и выясняем правду живым прогоном. Облачные модели всегда
    «установлены»: Ollama тянет их по требованию, и в ``ollama list`` их
    может не быть (проверено на ``gpt-oss:20b:cloud``).
    """
    if installed is None or is_cloud_model(model):
        return True
    names = {str(name).strip().lower() for name in installed}
    target = (model or "").strip().lower()
    if target in names:
        return True
    # ``ollama list`` печатает полный тег, а цепочка может ссылаться на модель
    # без ``:latest`` — и наоборот.
    bare = target.removesuffix(":latest")
    return bare in names or f"{bare}:latest" in names


def _result(
    model: str,
    *,
    ok: bool,
    status: str,
    text: str = "",
    error: str | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    return {
        "model": model,
        "ok": ok,
        "status": status,
        "text": text,
        "error": error,
        "timeout_s": timeout_s,
    }


def _call_ollama(
    model: str,
    prompt: str,
    *,
    timeout: float | None = None,
    popen: type = subprocess.Popen,
    runner: Callable[..., tuple[int, str, str]] | None = None,
) -> dict[str, Any]:
    """Вызвать модель через ``ollama run`` и классифицировать исход.

    Возвращает словарь ``{model, ok, status, text, error, timeout_s}``;
    исключения наружу не выпускает.
    """
    effective_timeout = model_timeout(model) if timeout is None else timeout
    run = runner if runner is not None else partial(run_model, popen=popen)

    try:
        returncode, stdout, stderr = run(model, prompt, timeout=effective_timeout)
    except subprocess.TimeoutExpired:
        return _result(
            model,
            ok=False,
            status=STATUS_TIMEOUT,
            error=f"timeout after {effective_timeout}s",
            timeout_s=effective_timeout,
        )
    except FileNotFoundError as exc:
        return _result(
            model,
            ok=False,
            status=STATUS_ERROR,
            error=f"ollama not found: {exc}",
            timeout_s=effective_timeout,
        )
    except OSError as exc:
        return _result(
            model, ok=False, status=STATUS_ERROR, error=str(exc), timeout_s=effective_timeout
        )

    text = (stdout or "").strip()
    err = (stderr or "").strip()

    reason = provider_unavailable_reason(err) or provider_unavailable_reason(text)
    if reason and not text:
        return _result(
            model,
            ok=False,
            status=STATUS_SKIPPED,
            error=f"skipped: {reason}",
            timeout_s=effective_timeout,
        )

    if returncode != 0:
        return _result(
            model,
            ok=False,
            status=STATUS_ERROR,
            text=text,
            error=err or f"ollama exited with code {returncode}",
            timeout_s=effective_timeout,
        )
    if not text:
        return _result(
            model,
            ok=False,
            status=STATUS_ERROR,
            error="empty response",
            timeout_s=effective_timeout,
        )
    return _result(model, ok=True, status=STATUS_OK, text=text, timeout_s=effective_timeout)


def _call_xai(
    model: str,
    prompt: str,
    *,
    timeout: float | None = None,
    api_key: str | None = None,
    env: Mapping[str, str] | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Вызвать модель xAI (Grok) по HTTP.

    Без ``XAI_API_KEY`` модель не ошибка, а ``skipped``: ключа на хосте нет
    штатно, и ронять из-за этого ежедневную задачу незачем.
    """
    effective_timeout = model_timeout(model) if timeout is None else timeout
    environ = env if env is not None else os.environ
    key = api_key or environ.get("XAI_API_KEY")
    if not key:
        return _result(
            model,
            ok=False,
            status=STATUS_SKIPPED,
            error="skipped: XAI_API_KEY missing",
            timeout_s=effective_timeout,
        )

    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        XAI_API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with opener(request, timeout=effective_timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = decode_console_output(exc.read() if hasattr(exc, "read") else b"")
        if exc.code in (401, 402, 403):
            return _result(
                model,
                ok=False,
                status=STATUS_SKIPPED,
                error=f"skipped: xAI rejected the key (HTTP {exc.code})",
                timeout_s=effective_timeout,
            )
        return _result(
            model,
            ok=False,
            status=STATUS_ERROR,
            error=f"HTTP {exc.code}: {detail.strip()[:200]}" if detail else f"HTTP {exc.code}",
            timeout_s=effective_timeout,
        )
    except TimeoutError:
        return _result(
            model,
            ok=False,
            status=STATUS_TIMEOUT,
            error=f"timeout after {effective_timeout}s",
            timeout_s=effective_timeout,
        )
    except (urllib.error.URLError, OSError) as exc:
        return _result(
            model, ok=False, status=STATUS_ERROR, error=str(exc), timeout_s=effective_timeout
        )

    try:
        data = json.loads(decode_console_output(body))
        text = str(data["choices"][0]["message"]["content"]).strip()
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        return _result(
            model,
            ok=False,
            status=STATUS_ERROR,
            error=f"malformed xAI response: {exc}",
            timeout_s=effective_timeout,
        )

    if not text:
        return _result(
            model,
            ok=False,
            status=STATUS_ERROR,
            error="empty response",
            timeout_s=effective_timeout,
        )
    return _result(model, ok=True, status=STATUS_OK, text=text, timeout_s=effective_timeout)


def call_chain(
    prompt: str,
    models: Sequence[str] | None = None,
    *,
    timeout: float | None = None,
    timeouts: Mapping[str, float] | None = None,
    env: Mapping[str, str] | None = None,
    installed: Iterable[str] | None = None,
    ollama_caller: Callable[..., dict[str, Any]] | None = None,
    xai_caller: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Обойти цепочку и вернуть первый полученный ответ.

    ``timeout=None`` — персональный таймаут каждой модели. Результат:
    ``{ok, model, text, attempts, error}``, где ``attempts`` — исход каждой
    попытки (включая ``skipped`` / ``not_installed``).
    """
    chain = chain_models(models)
    call_ollama = ollama_caller or _call_ollama
    call_xai = xai_caller or _call_xai

    attempts: list[dict[str, Any]] = []
    for model in chain:
        step_timeout = model_timeout(model, timeouts) if timeout is None else timeout
        if is_xai_model(model):
            result = call_xai(model, prompt, timeout=step_timeout, env=env)
        elif not is_model_installed(model, installed):
            result = _result(
                model,
                ok=False,
                status=STATUS_NOT_INSTALLED,
                error=f"not installed locally: run `ollama pull {model}`",
                timeout_s=step_timeout,
            )
        else:
            result = call_ollama(model, prompt, timeout=step_timeout)
        attempts.append(result)
        if result.get("ok"):
            return {
                "ok": True,
                "model": model,
                "text": result.get("text", ""),
                "attempts": attempts,
                "error": None,
            }

    return {
        "ok": False,
        "model": None,
        "text": "",
        "attempts": attempts,
        "error": "all models in the chain failed",
    }


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Мини-парсер YAML на случай, когда PyYAML недоступен.

    Понимает только тот формат, в котором пишется конфиг цепочки: верхний
    уровень — отображение, значения — скаляр, список ``- item`` или вложенное
    отображение ``key: value``. Имена моделей содержат двоеточия, поэтому
    ключ отделяется от значения по первому ``": "`` (двоеточие с пробелом).
    """
    data: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.split(" #", 1)[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indented = line[:1].isspace()
        stripped = line.strip()

        if not indented:
            key, _, inline = stripped.partition(":")
            if not _:
                continue
            current_key = key.strip()
            value = inline.strip()
            data[current_key] = _scalar(value) if value else None
            continue

        if current_key is None:
            continue
        if stripped.startswith("- "):
            bucket = data.get(current_key)
            if not isinstance(bucket, list):
                bucket = []
                data[current_key] = bucket
            bucket.append(_scalar(stripped[2:].strip()))
            continue

        sub_key, separator, sub_value = stripped.partition(": ")
        if not separator:
            sub_key, separator, sub_value = stripped.rstrip(":"), ":", ""
        bucket = data.get(current_key)
        if not isinstance(bucket, dict):
            bucket = {}
            data[current_key] = bucket
        bucket[sub_key.strip()] = _scalar(sub_value.strip())
    return data


def _scalar(value: str) -> Any:
    """Привести YAML-скаляр к числу/булеву/строке без внешних зависимостей."""
    text = value.strip().strip('"').strip("'")
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    if text.lower() in {"null", "none", "~", ""}:
        return None if text.lower() != "" else ""
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def load_chain_yaml(path: str | Path | None = None) -> dict[str, Any]:
    """Прочитать конфиг цепочки из YAML/JSON.

    Порядок поиска: явный *path* → ``$OLLAMA_CHAIN_CONFIG`` →
    ``config/ollama_chain.yaml`` в корне репозитория. Файла нет — возвращаются
    дефолты модуля, так что клон репозитория работает без конфига.

    Возвращает ``{chain, timeouts, prompt, source}``.
    """
    candidate = path or os.environ.get(CHAIN_CONFIG_ENV) or DEFAULT_CHAIN_CONFIG
    config_path = Path(candidate)

    defaults: dict[str, Any] = {
        "chain": DEFAULT_CHAIN,
        "timeouts": dict(MODEL_TIMEOUTS),
        "prompt": None,
        "source": None,
    }
    if not config_path.is_file():
        return defaults

    raw = config_path.read_text(encoding="utf-8")
    if config_path.suffix.lower() == ".json":
        parsed: Any = json.loads(raw)
    else:
        try:  # PyYAML есть в requirements, но модуль обязан работать и без него
            import yaml  # type: ignore[import-untyped]

            parsed = yaml.safe_load(raw)
        except ImportError:
            parsed = _parse_simple_yaml(raw)

    if not isinstance(parsed, dict):
        return defaults

    chain = parsed.get("chain") or parsed.get("models")
    if isinstance(chain, (list, tuple)) and chain:
        defaults["chain"] = tuple(str(item) for item in chain)

    timeouts = parsed.get("timeouts") or parsed.get("model_timeouts")
    if isinstance(timeouts, dict):
        merged = dict(MODEL_TIMEOUTS)
        for key, value in timeouts.items():
            try:
                merged[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        defaults["timeouts"] = merged

    prompt = parsed.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        defaults["prompt"] = prompt.strip()

    defaults["source"] = str(config_path)
    return defaults


__all__ = [
    "CLOUD_TIMEOUT_SECONDS",
    "DEFAULT_CHAIN",
    "DEFAULT_TIMEOUT_SECONDS",
    "LOCAL_TIMEOUT_SECONDS",
    "MODEL_TIMEOUTS",
    "PROBE_STATUSES",
    "STATUS_ERROR",
    "STATUS_NOT_INSTALLED",
    "STATUS_OK",
    "STATUS_SKIPPED",
    "STATUS_TIMEOUT",
    "_call_ollama",
    "_call_xai",
    "call_chain",
    "chain_models",
    "is_cloud_model",
    "is_model_installed",
    "is_xai_model",
    "list_installed_models",
    "load_chain_yaml",
    "model_timeout",
    "provider_unavailable_reason",
    "run_model",
]
