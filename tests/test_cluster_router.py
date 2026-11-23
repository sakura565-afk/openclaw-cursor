"""Маршрутизация профилей cluster.router (офлайн)."""

from __future__ import annotations

import pytest

from scripts.cluster.router import list_profiles, route

_EXPECTED = {
    "gpu-heavy": "work",
    "backoffice": "home",
    "vision-asset": "home",
    "audio-music": "home",
    "obsidian": "home",
}


@pytest.mark.parametrize("profile,machine", list(_EXPECTED.items()))
def test_route_known_profiles(profile: str, machine: str) -> None:
    target, reason = route(profile)
    assert target == machine
    assert reason


def test_route_unknown_falls_back_to_home() -> None:
    target, reason = route("totally-unknown-skill")
    assert target == "home"
    assert "не в таблице" in reason or "неизвестный" in reason.lower() or "по умолчанию" in reason


def test_route_empty_profile() -> None:
    target, reason = route("")
    assert target == "home"
    assert reason


def test_list_profiles_covers_table() -> None:
    names = set(list_profiles())
    assert names == set(_EXPECTED.keys())