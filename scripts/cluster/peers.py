"""Описание пиров кластера и чтение ключей из hermes/.env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Peer:
    """Удалённый или локальный Hermes peer."""

    name: str
    """Логическое имя: work, home, homepeer."""

    base_url: str
    """Базовый URL API (без завершающего слэша)."""

    profile: str
    """Имя профиля Hermes на хосте (main / home / homepeer)."""

    api_key: str
    """Bearer-токен для Funnel; пустая строка — локальный peer без авторизации."""

    machine_label: str
    """Метка машины для маршрутизатора: work или home."""

    hostname: str = ""
    """Человекочитаемое имя Windows-хоста (для отчётов)."""


_HOME_FUNNEL = "https://desktop-6ecjbt6.tailcaa609.ts.net"
_DEFAULT_WORK_URL = os.environ.get("HERMES_PEER_WORK_URL", "http://127.0.0.1:8765")

_PEER_SPECS: dict[str, dict[str, str]] = {
    "work": {
        "base_url": _DEFAULT_WORK_URL,
        "profile": "work",
        "env_key": "HERMES_PEER_WORK_KEY",
        "machine_label": "work",
        "hostname": "DESKTOP-J25T962",
    },
    "home": {
        "base_url": _HOME_FUNNEL,
        "profile": "home",
        "env_key": "HERMES_PEER_HOME_KEY",
        "machine_label": "home",
        "hostname": "DESKTOP-6ECJBT6",
    },
    "homepeer": {
        "base_url": _HOME_FUNNEL,
        "profile": "homepeer",
        "env_key": "HERMES_PEER_HOMEPEER_KEY",
        "machine_label": "home",
        "hostname": "DESKTOP-6ECJBT6",
    },
}


def _hermes_env_path() -> Path:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / "hermes" / ".env"
    return Path.home() / ".local" / "share" / "hermes" / ".env"


def load_hermes_dotenv() -> dict[str, str]:
    """Простой парсер KEY=VALUE из %LOCALAPPDATA%/hermes/.env (без внешних зависимостей)."""
    path = _hermes_env_path()
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            out[key] = value
    return out


def _api_key_for(env_var: str, dotenv: dict[str, str]) -> str:
    if env_var in os.environ:
        return os.environ[env_var].strip()
    return dotenv.get(env_var, "").strip()


def resolve_peer(name: str) -> Peer:
    """Вернуть peer по имени; KeyError при неизвестном имени."""
    key = name.strip().lower()
    spec = _PEER_SPECS.get(key)
    if spec is None:
        raise KeyError(f"неизвестный peer: {name!r}; ожидаются: {', '.join(_PEER_SPECS)}")

    dotenv = load_hermes_dotenv()
    env_key_name = spec["env_key"]
    api_key = _api_key_for(env_key_name, dotenv)
    # Локальный work может работать без ключа.
    base_url = os.environ.get(f"HERMES_PEER_{key.upper()}_URL", spec["base_url"]).rstrip("/")

    return Peer(
        name=key,
        base_url=base_url,
        profile=spec["profile"],
        api_key=api_key,
        machine_label=spec["machine_label"],
        hostname=spec.get("hostname", ""),
    )


def list_peer_names() -> tuple[str, ...]:
    return tuple(_PEER_SPECS.keys())