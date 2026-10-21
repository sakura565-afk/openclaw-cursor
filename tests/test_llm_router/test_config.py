"""Конфиг: слияние файла и env, запрет секретов, чтение токена."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.llm_router.config import (
    ConfigError,
    MissingTokenError,
    RouterConfig,
    load_config,
    read_token_from_auth_file,
    resolve_access_token,
)


def test_defaults_match_current_stack() -> None:
    config = RouterConfig()

    assert config.default_model == "MiniMax-M3.1-Flash"
    assert config.vision_model == "MiniMax-Vision-32B"
    assert config.api_url == "https://api.minimax.io/anthropic/v1/messages"
    assert config.anthropic_version == "2023-06-01"
    assert config.auth_provider == "minimax-oauth"
    assert config.vision_max_tokens == 6000


def test_repo_config_file_loads_without_secrets() -> None:
    config = load_config(env={})

    assert config.default_model == "MiniMax-M3.1-Flash"
    assert config.telemetry_path == "data/llm_router_telemetry.jsonl"


def test_env_overrides_file(tmp_path: Path) -> None:
    path = tmp_path / "router.json"
    path.write_text(
        json.dumps({"default_model": "from-file", "cache_size": 8, "cache_persist": False}),
        encoding="utf-8",
    )

    config = load_config(
        path,
        env={
            "LLM_ROUTER_DEFAULT_MODEL": "from-env",
            "LLM_ROUTER_CACHE_SIZE": "64",
            "LLM_ROUTER_CACHE_PERSIST": "true",
        },
    )

    assert config.default_model == "from-env"
    assert config.cache_size == 64
    assert config.cache_persist is True


def test_secret_in_config_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "router.json"
    path.write_text(json.dumps({"access_token": "sk-leaked"}), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(path, env={})
    assert "access_token" in str(excinfo.value)

    # «max_tokens» секретом не считается.
    ok_path = tmp_path / "ok.json"
    ok_path.write_text(
        json.dumps({"effort_profiles": {"low": {"max_tokens": 10}}}), encoding="utf-8"
    )
    assert load_config(ok_path, env={}).effort_profiles["low"]["max_tokens"] == 10


def test_token_comes_from_auth_json(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {"providers": {"minimax-oauth": {"access_token": "token-from-auth", "type": "oauth"}}}
        ),
        encoding="utf-8",
    )

    assert read_token_from_auth_file(auth) == "token-from-auth"
    assert resolve_access_token(env={"HERMES_AUTH_JSON": str(auth), "HOME": str(tmp_path)}) == (
        "token-from-auth"
    )
    # env имеет приоритет над файлом.
    assert (
        resolve_access_token(
            env={"MINIMAX_ACCESS_TOKEN": "token-from-env", "HERMES_AUTH_JSON": str(auth)}
        )
        == "token-from-env"
    )


def test_missing_token_raises_or_returns_none(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}

    assert resolve_access_token(env=env, required=False) is None
    with pytest.raises(MissingTokenError):
        resolve_access_token(env=env)


def test_invalid_strategy_rejected() -> None:
    with pytest.raises(ConfigError):
        RouterConfig(classifier_strategy="magic")
