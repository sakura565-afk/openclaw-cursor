"""Отправка задач на Hermes peer: сессия, async run, опрос статуса."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .peers import Peer

TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "done",
        "failed",
        "error",
        "cancelled",
        "canceled",
        "stopped",
        "finished",
        "succeeded",
    }
)

SESSION_TITLE = "Bot Chat"


@dataclass(slots=True)
class RunResult:
    peer: str
    run_id: str
    status: str
    output: str | None
    elapsed_s: float
    error: str | None = None


def _headers(peer: Peer, extra: dict[str, str] | None = None) -> dict[str, str]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if peer.api_key:
        h["Authorization"] = f"Bearer {peer.api_key}"
    if extra:
        h.update(extra)
    return h


def _request(
    peer: Peer,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 120,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    url = f"{peer.base_url}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(url, data=data, method=method, headers=_headers(peer, extra_headers))
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return {}
    return json.loads(raw)


def ensure_bot_chat_session(peer: Peer, *, timeout: float = 60) -> str:
    """Найти сессию «Bot Chat» или создать новую; вернуть session_id."""
    query = "/api/sessions?title=Bot+Chat&include_hidden=1"
    try:
        payload = _request(peer, "GET", query, timeout=timeout)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"не удалось получить список сессий: {exc}") from exc

    sessions: list[Any]
    if isinstance(payload, list):
        sessions = payload
    elif isinstance(payload, dict):
        sessions = payload.get("sessions") or payload.get("data") or []
    else:
        sessions = []

    for item in sessions:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", ""))
        if title == SESSION_TITLE:
            sid = item.get("id") or item.get("session_id")
            if sid is not None:
                return str(sid)

    try:
        created = _request(
            peer,
            "POST",
            "/api/sessions",
            {"title": SESSION_TITLE, "hidden": True},
            timeout=timeout,
        )
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"не удалось создать сессию Bot Chat: {exc}") from exc

    if isinstance(created, dict):
        sid = created.get("id") or created.get("session_id")
        if sid is not None:
            return str(sid)
    raise RuntimeError(f"создание сессии вернуло неожиданный ответ: {created!r}")


def _extract_run_id(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("run_id", "id", "runId"):
            if payload.get(key):
                return str(payload[key])
        data = payload.get("data")
        if isinstance(data, dict) and data.get("run_id"):
            return str(data["run_id"])
    raise RuntimeError(f"POST /v1/runs не вернул run_id: {payload!r}")


def _normalize_status(payload: dict[str, Any]) -> tuple[str, str | None, str | None]:
    status = str(
        payload.get("status")
        or payload.get("state")
        or (payload.get("data") or {}).get("status")
        or "unknown"
    ).lower()
    output = payload.get("output")
    if output is None and isinstance(payload.get("data"), dict):
        output = payload["data"].get("output")
    if output is not None and not isinstance(output, str):
        output = json.dumps(output, ensure_ascii=False)

    err = payload.get("error")
    if err is None:
        err = payload.get("message")
    if err is not None and not isinstance(err, str):
        err = json.dumps(err, ensure_ascii=False)
    return status, output, err


def run_task(
    peer: Peer,
    prompt: str,
    *,
    wait: bool = True,
    timeout: float = 600,
    poll: float = 4,
) -> RunResult:
    """Отправить prompt на peer и при wait=True дождаться терминального статуса."""
    session_id = ensure_bot_chat_session(peer)
    idem = str(uuid.uuid4())
    body = {"prompt": prompt, "session_id": session_id}
    t0 = time.monotonic()

    try:
        started = _request(
            peer,
            "POST",
            "/v1/runs",
            body,
            timeout=min(120, timeout),
            extra_headers={"Idempotency-Key": idem},
        )
        run_id = _extract_run_id(started)
    except Exception as exc:  # noqa: BLE001 — верхний уровень CLI
        return RunResult(
            peer=peer.name,
            run_id="",
            status="error",
            output=None,
            elapsed_s=time.monotonic() - t0,
            error=str(exc),
        )

    if not wait:
        return RunResult(
            peer=peer.name,
            run_id=run_id,
            status="submitted",
            output=None,
            elapsed_s=time.monotonic() - t0,
            error=None,
        )

    deadline = t0 + timeout
    last_status = "running"
    last_output: str | None = None
    last_error: str | None = None

    while time.monotonic() < deadline:
        try:
            payload = _request(peer, "GET", f"/v1/runs/{run_id}", timeout=min(60, poll * 4))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            time.sleep(poll)
            continue

        if not isinstance(payload, dict):
            time.sleep(poll)
            continue

        last_status, last_output, last_error = _normalize_status(payload)
        if last_status in TERMINAL_STATUSES or last_output or last_error:
            return RunResult(
                peer=peer.name,
                run_id=run_id,
                status=last_status,
                output=last_output,
                elapsed_s=time.monotonic() - t0,
                error=last_error,
            )
        time.sleep(poll)

    return RunResult(
        peer=peer.name,
        run_id=run_id,
        status="timeout",
        output=last_output,
        elapsed_s=time.monotonic() - t0,
        error=last_error or f"превышен timeout {timeout}s (последний статус: {last_status})",
    )