"""Проверка здоровья M1 (work) и M2 (home): GPU, ComfyUI, Ollama, Hermes peer."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from .peers import Peer, resolve_peer

COMFYUI_LOCAL = os.environ.get("COMFYUI_PROD", "http://127.0.0.1:8188").rstrip("/")
OLLAMA_LOCAL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def _http_json(url: str, *, timeout: float = 15, bearer: str = "") -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw.strip() else {}


def parse_nvidia_smi_csv(text: str) -> dict[str, Any]:
    """Разбор вывода nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader,nounits."""
    line = ""
    for raw in text.splitlines():
        if raw.strip():
            line = raw.strip()
            break
    if not line:
        return {"available": False, "reason": "пустой вывод nvidia-smi"}
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 3:
        return {"available": False, "reason": f"неожиданный формат: {line!r}"}
    name, total_s, used_s = parts[0], parts[1], parts[2]
    try:
        total_mib = float(total_s)
        used_mib = float(used_s)
    except ValueError:
        return {"available": False, "reason": f"не числа VRAM: {parts!r}"}
    return {
        "available": True,
        "name": name,
        "vram_total_mib": total_mib,
        "vram_used_mib": used_mib,
        "vram_free_mib": max(0.0, total_mib - used_mib),
    }


def probe_nvidia_smi() -> dict[str, Any]:
    """Локальный nvidia-smi; при отсутствии GPU — status без исключения."""
    if shutil.which("nvidia-smi") is None:
        return {"available": False, "reason": "nvidia-smi не найден в PATH"}
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "reason": str(exc)}
    if proc.returncode != 0:
        return {
            "available": False,
            "reason": (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip(),
        }
    return parse_nvidia_smi_csv(proc.stdout)


def probe_comfyui_system_stats(base_url: str = COMFYUI_LOCAL) -> dict[str, Any]:
    """GET /system_stats — код 200 и версия ComfyUI."""
    url = f"{base_url}/system_stats"
    try:
        payload = _http_json(url)
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "error": str(exc)}
    version = None
    if isinstance(payload, dict):
        version = payload.get("system", {}).get("comfyui_version") or payload.get(
            "comfyui_version"
        )
    return {"reachable": True, "http_status": 200, "comfyui_version": version, "raw_keys": list(payload)[:8]}


def probe_ollama_tags(base_url: str = OLLAMA_LOCAL) -> dict[str, Any]:
    """GET /api/tags — число локальных моделей."""
    url = f"{base_url}/api/tags"
    try:
        payload = _http_json(url)
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "error": str(exc)}
    models = payload.get("models") if isinstance(payload, dict) else None
    count = len(models) if isinstance(models, list) else 0
    return {"reachable": True, "model_count": count}


def probe_peer_remote(peer: Peer) -> dict[str, Any]:
    """Удалённо: /health и /v1/capabilities."""
    out: dict[str, Any] = {"peer": peer.name, "base_url": peer.base_url}
    health: dict[str, Any] = {"reachable": False}
    caps: dict[str, Any] = {"reachable": False}

    for label, path in (("health", "/health"), ("capabilities", "/v1/capabilities")):
        url = f"{peer.base_url}{path}"
        try:
            req = Request(
                url,
                headers={
                    "Accept": "application/json",
                    **(
                        {"Authorization": f"Bearer {peer.api_key}"}
                        if peer.api_key
                        else {}
                    ),
                },
                method="GET",
            )
            with urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                code = resp.getcode()
            try:
                body = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                body = {"raw": raw[:500]}
            block = {"reachable": True, "http_status": code, "body": body}
        except URLError as exc:
            block = {"reachable": False, "status": "unreachable", "error": str(exc.reason)}
        except Exception as exc:  # noqa: BLE001
            block = {"reachable": False, "status": "unreachable", "error": str(exc)}

        if label == "health":
            health = block
        else:
            caps = block

    out["health"] = health
    out["capabilities"] = caps
    if not health.get("reachable") and not caps.get("reachable"):
        out["status"] = "unreachable"
    elif health.get("reachable") or caps.get("reachable"):
        out["status"] = "ok"
    else:
        out["status"] = "degraded"
    return out


def _summarize_local_block() -> dict[str, Any]:
    gpu = probe_nvidia_smi()
    comfy = probe_comfyui_system_stats()
    ollama = probe_ollama_tags()
    ok_bits = [
        gpu.get("available"),
        comfy.get("reachable"),
        ollama.get("reachable"),
    ]
    if any(ok_bits):
        status = "ok" if all(x for x in ok_bits if x is not None) else "degraded"
    else:
        status = "unreachable"
    return {
        "status": status,
        "role": "work",
        "hostname_hint": "DESKTOP-J25T962",
        "gpu": gpu,
        "comfyui": comfy,
        "ollama": ollama,
    }


def check_all() -> dict[str, Any]:
    """Сводка по work (локальные пробы) и home (удалённый peer). Не бросает при сетевых сбоях."""
    work = _summarize_local_block()
    home_peer = resolve_peer("home")
    home_remote = probe_peer_remote(home_peer)
    home = {
        "status": home_remote.get("status", "unreachable"),
        "role": "home",
        "hostname_hint": home_peer.hostname,
        "hermes": home_remote,
        # ComfyUI на M2 доступен только с того хоста; с work-машины — не проверяем напрямую.
        "comfyui_note": "ComfyUI M2 (:8188) — только на хосте DESKTOP-6ECJBT6",
    }
    return {"work": work, "home": home}