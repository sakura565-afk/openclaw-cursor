"""Маршрутизация профилей навыков между M1 (work) и M2 (home)."""

from __future__ import annotations

# peer_name -> (machine, reason)
RouteDecision = tuple[str, str]

_PROFILE_ROUTES: dict[str, RouteDecision] = {
    "gpu-heavy": (
        "work",
        "16 ГБ VRAM на M1: qwen_image_2.1_int8, z_image_turbo, flux1-fill-dev-Q8_0, "
        "minimax_h3_fl2va/ref2va",
    ),
    "backoffice": (
        "home",
        "M2 OpenClaw: 1С, Google Sheets, Obsidian, кроны, отчёты",
    ),
    "vision-asset": (
        "home",
        "ComfyUI M2: LanPaint, BrushNet, SAM2, RMBG, BiRefNet, Florence2, flux1-fill-dev-Q4_K_S",
    ),
    "audio-music": (
        "home",
        "minimax_music3_dit на M2",
    ),
    "obsidian": (
        "home",
        "Obsidianstore 2410 .md и навыки на M2",
    ),
}

_DEFAULT_ROUTE: RouteDecision = (
    "home",
    "неизвестный профиль: по умолчанию backoffice/home (OpenClaw, Obsidian, 114 тегов Ollama)",
)


def route(profile: str) -> RouteDecision:
    """Выбрать целевую машину (work|home) и текстовое обоснование."""
    key = profile.strip().lower()
    if not key:
        return _DEFAULT_ROUTE[0], f"пустой профиль — {_DEFAULT_ROUTE[1]}"
    if key in _PROFILE_ROUTES:
        machine, reason = _PROFILE_ROUTES[key]
        return machine, reason
    return _DEFAULT_ROUTE[0], f"профиль {profile!r} не в таблице — {_DEFAULT_ROUTE[1]}"


def list_profiles() -> tuple[str, ...]:
    return tuple(_PROFILE_ROUTES.keys())