"""Конфигурация роутера: дефолты, JSON-файл в git и переопределения из env.

Приоритет значений (по возрастанию): дефолты датакласса -> ``data/llm_router_config.json``
-> переменные окружения ``LLM_ROUTER_*``.

Секретов в конфиг-файле быть не должно: токен читается только из env
(``MINIMAX_ACCESS_TOKEN`` / ``MINIMAX_API_KEY``) или из hermes ``auth.json``
(``providers.minimax-oauth.access_token``). Если в JSON-файле встречается
секретоподобный ключ — загрузка падает с :class:`ConfigError`, чтобы токен
случайно не попал в репозиторий.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "data" / "llm_router_config.json"

#: Сегменты ключей, запрещённые в конфиге под git. Сравнение по сегментам,
#: чтобы безобидный ``max_tokens`` не считался секретом, а ``access_token`` — считался.
SECRET_KEY_SEGMENTS = frozenset(
    {
        "token",
        "secret",
        "secrets",
        "password",
        "passwd",
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "bearer",
    }
)
#: Пары соседних сегментов, которые вместе означают секрет.
SECRET_KEY_PAIRS = frozenset({("api", "key"), ("access", "key"), ("private", "key")})
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}$")

ENV_PREFIX = "LLM_ROUTER_"
TOKEN_ENV_VARS = ("MINIMAX_ACCESS_TOKEN", "MINIMAX_API_KEY", "LLM_ROUTER_ACCESS_TOKEN")
AUTH_PATH_ENV_VAR = "HERMES_AUTH_JSON"


class ConfigError(RuntimeError):
    """Конфиг нечитаем, повреждён или содержит секреты."""


class MissingTokenError(RuntimeError):
    """Токен доступа не найден ни в env, ни в ``auth.json``."""


def _default_effort_profiles() -> Dict[str, Dict[str, int]]:
    """Профили effort: во сколько токенов упирается ответ каждого уровня."""

    return {
        "low": {"max_tokens": 1024},
        "medium": {"max_tokens": 4096},
        "high": {"max_tokens": 8192},
    }


@dataclass
class RouterConfig:
    """Все настройки оркестратора в одном месте (без секретов)."""

    # --- модели и endpoint ---
    default_model: str = "MiniMax-M3.1-Flash"
    deep_model: str = "MiniMax-M3.1-Flash"
    vision_model: str = "MiniMax-Vision-32B"
    classifier_model: str = "MiniMax-M3.1-Flash"
    api_url: str = "https://api.minimax.io/anthropic/v1/messages"
    anthropic_version: str = "2023-06-01"
    auth_provider: str = "minimax-oauth"

    # --- effort ---
    default_effort: str = "medium"
    fast_effort: str = "low"
    deep_effort: str = "high"
    effort_profiles: Dict[str, Dict[str, int]] = field(default_factory=_default_effort_profiles)

    # --- таймауты и ретраи ---
    request_timeout: float = 60.0
    classifier_timeout: float = 15.0
    vision_timeout: float = 120.0
    max_attempts: int = 2
    retry_backoff_sec: float = 1.0
    retry_backoff_factor: float = 2.0
    parallel_workers: int = 4

    # --- кеш ---
    cache_size: int = 256
    cache_ttl_sec: float = 3600.0
    cache_persist: bool = False
    cache_path: str = "data/llm_cache.json"

    # --- классификатор ---
    classifier_strategy: str = "cascade"
    classifier_min_confidence: float = 0.65
    rule_deep_length: int = 400

    # --- judge / эскалация ---
    escalation_threshold: float = 0.6
    cascade_min_confidence: float = 0.5
    candidate_models: List[str] = field(
        default_factory=lambda: ["MiniMax-M3.1-Flash", "MiniMax-M3.1-Flash"]
    )

    # --- vision ---
    vision_max_tokens: int = 6000

    # --- телеметрия ---
    telemetry_path: str = "data/llm_router_telemetry.jsonl"
    telemetry_archive_dir: str = "data/llm_router_telemetry"
    telemetry_max_bytes: int = 5 * 1024 * 1024
    telemetry_enabled: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ConfigError("max_attempts должен быть >= 1")
        if not 0.0 <= self.escalation_threshold <= 1.0:
            raise ConfigError("escalation_threshold должен лежать в [0, 1]")
        if self.classifier_strategy not in {"rule", "embedding", "llm", "cascade"}:
            raise ConfigError(f"неизвестная стратегия классификатора: {self.classifier_strategy}")
        # Vision-модель молчит, если бюджет токенов съеден thinking-блоком.
        self.vision_max_tokens = min(int(self.vision_max_tokens), 6000)

    # --- производные пути ---

    def resolve_path(self, value: str) -> Path:
        """Относительные пути считаются от корня репозитория."""

        path = Path(value).expanduser()
        return path if path.is_absolute() else (REPO_ROOT / path)

    @property
    def cache_file(self) -> Path:
        return self.resolve_path(self.cache_path)

    @property
    def telemetry_file(self) -> Path:
        return self.resolve_path(self.telemetry_path)

    @property
    def telemetry_archive(self) -> Path:
        return self.resolve_path(self.telemetry_archive_dir)

    # --- effort / tier ---

    def model_for_tier(self, tier: str) -> str:
        """Модель для tier: ``deep`` может быть тяжелее, чем ``fast``."""

        return self.deep_model if tier == "deep" else self.default_model

    def effort_for_tier(self, tier: str) -> str:
        return self.deep_effort if tier == "deep" else self.fast_effort

    def max_tokens_for_effort(self, effort: Optional[str]) -> int:
        profile = self.effort_profiles.get(effort or self.default_effort)
        if not profile:
            profile = self.effort_profiles.get(self.default_effort, {})
        return int(profile.get("max_tokens", 4096))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _coerce(name: str, raw: Any, current: Any) -> Any:
    """Привести значение из JSON/env к типу текущего дефолта."""

    if isinstance(current, bool):
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(current, int) and not isinstance(current, bool):
        return int(float(raw))
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, (list, dict)):
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ConfigError(f"поле {name}: ожидался JSON, получено {raw!r}") from exc
        if not isinstance(raw, type(current)):
            raise ConfigError(f"поле {name}: ожидался {type(current).__name__}")
        return raw
    return str(raw)


def _is_secret_key(key: str) -> bool:
    """Похож ли ключ на секрет (``access_token``, ``api_key``, ...)."""

    segments = [part for part in re.split(r"[^a-z0-9]+", key.lower()) if part]
    if any(segment in SECRET_KEY_SEGMENTS for segment in segments):
        return True
    return any(pair in SECRET_KEY_PAIRS for pair in zip(segments, segments[1:]))


def _assert_no_secrets(payload: Any, source: Path, prefix: str = "") -> None:
    """Рекурсивно проверить, что в конфиге нет ни секретных ключей, ни JWT."""

    if isinstance(payload, Mapping):
        for key, value in payload.items():
            path = f"{prefix}{key}"
            if _is_secret_key(str(key)):
                raise ConfigError(
                    f"в {source} найден секретоподобный ключ {path!r}; "
                    "токен должен приходить из env или auth.json"
                )
            _assert_no_secrets(value, source, prefix=f"{path}.")
        return
    if isinstance(payload, list):
        for index, item in enumerate(payload):
            _assert_no_secrets(item, source, prefix=f"{prefix}{index}.")
        return
    if isinstance(payload, str) and _JWT_RE.match(payload.strip()):
        raise ConfigError(
            f"в {source} по пути {prefix.rstrip('.')!r} лежит строка, похожая на JWT; "
            "секреты в git не коммитим"
        )


def load_config(
    path: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    *,
    overrides: Optional[Mapping[str, Any]] = None,
) -> RouterConfig:
    """Собрать :class:`RouterConfig` из файла, env и явных переопределений.

    ``path=None`` означает ``data/llm_router_config.json``; отсутствие файла —
    не ошибка, просто остаются дефолты.
    """

    env = os.environ if env is None else env
    known = {f.name: f for f in fields(RouterConfig)}
    defaults = RouterConfig()
    values: Dict[str, Any] = {}

    config_path = DEFAULT_CONFIG_PATH if path is None else Path(path)
    if config_path.is_file():
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{config_path} не парсится как JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ConfigError(f"{config_path}: ожидался JSON-объект")
        _assert_no_secrets(payload, config_path)
        for key, raw in payload.items():
            if key.startswith("_") or key not in known:
                continue  # «_comment» и незнакомые ключи игнорируем молча
            values[key] = _coerce(key, raw, getattr(defaults, key))

    for key in known:
        env_name = f"{ENV_PREFIX}{key.upper()}"
        if env_name in env:
            values[key] = _coerce(key, env[env_name], getattr(defaults, key))

    for key, raw in (overrides or {}).items():
        if key not in known:
            raise ConfigError(f"неизвестное поле конфига: {key}")
        values[key] = raw

    return RouterConfig(**values)


def default_auth_paths(env: Optional[Mapping[str, str]] = None) -> List[Path]:
    """Кандидаты расположения hermes ``auth.json`` для Windows и POSIX."""

    env = os.environ if env is None else env
    candidates: List[Path] = []
    explicit = env.get(AUTH_PATH_ENV_VAR, "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())
    local_appdata = env.get("LOCALAPPDATA", "").strip()
    if local_appdata:
        candidates.append(Path(local_appdata) / "hermes" / "auth.json")
    home = Path(env.get("USERPROFILE") or env.get("HOME") or Path.home()).expanduser()
    candidates.extend(
        [
            home / "AppData" / "Local" / "hermes" / "auth.json",
            home / ".local" / "share" / "hermes" / "auth.json",
            home / ".config" / "hermes" / "auth.json",
            home / ".hermes" / "auth.json",
        ]
    )
    deduped: List[Path] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


def read_token_from_auth_file(path: Path, provider: str = "minimax-oauth") -> Optional[str]:
    """Достать ``providers.<provider>.access_token`` из ``auth.json``."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        return None
    entry = providers.get(provider)
    if isinstance(entry, str):
        return entry.strip() or None
    if isinstance(entry, dict):
        for key in ("access_token", "accessToken", "token", "api_key"):
            token = entry.get(key)
            if isinstance(token, str) and token.strip():
                return token.strip()
    return None


def resolve_access_token(
    config: Optional[RouterConfig] = None,
    env: Optional[Mapping[str, str]] = None,
    *,
    required: bool = True,
) -> Optional[str]:
    """Токен из env, иначе из ``auth.json``. В коде и в git его нет."""

    env = os.environ if env is None else env
    for name in TOKEN_ENV_VARS:
        token = env.get(name, "").strip()
        if token:
            return token

    provider = (config or RouterConfig()).auth_provider
    for candidate in default_auth_paths(env):
        if not candidate.is_file():
            continue
        token = read_token_from_auth_file(candidate, provider)
        if token:
            return token

    if required:
        raise MissingTokenError(
            "токен не найден: задайте MINIMAX_ACCESS_TOKEN или положите "
            f"providers.{provider}.access_token в hermes auth.json"
        )
    return None
