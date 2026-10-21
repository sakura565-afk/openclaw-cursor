"""HTTP-транспорт до MiniMax через anthropic-совместимый endpoint.

Адаптированная (не скопированная дословно) версия ``scripts/llm_call.py`` из
workspace: тот скрипт живёт вне репозитория, поэтому здесь лежит
самодостаточная реализация на stdlib — репозиторий и тесты не должны зависеть
от файла за его пределами.

Отличия от workspace-версии:

* модель по умолчанию ``MiniMax-M3.1-Flash`` (провайдер ``minimax-oauth``);
* ответ парсится перебором блоков по ``type == "text"``, а не как
  ``content[0]["text"]`` — у thinking-моделей первый блок не текстовый;
* HTTP-ошибки поднимаются как :class:`TransportHTTPError` с кодом статуса,
  чтобы вызывающий слой мог отличить 429/5xx (ретраить) от 451 (фолбэк) и
  прочих 4xx (не ретраить);
* никаких ``print``/``sys.exit`` — только исключения и возвращаемые значения.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.llm_router.config import RouterConfig, resolve_access_token

#: Коды, на которых ретрай осмыслен (остальные 4xx — нет).
RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504, 529})
VISION_BLOCKED_STATUS = 451


class TransportError(RuntimeError):
    """Базовая ошибка транспорта."""

    status_code: Optional[int] = None
    retryable: bool = False


class TransportHTTPError(TransportError):
    """HTTP-ответ с кодом ошибки."""

    def __init__(self, status_code: int, body: str = "", url: str = "") -> None:
        super().__init__(f"HTTP {status_code} от {url or 'endpoint'}: {body[:400]}")
        self.status_code = int(status_code)
        self.body = body
        self.url = url

    @property
    def retryable(self) -> bool:  # type: ignore[override]
        return self.status_code in RETRYABLE_STATUSES


class TransportNetworkError(TransportError):
    """Сеть недоступна/таймаут: ретрай допустим, кода статуса нет."""

    retryable = True


class TransportPayloadError(TransportError):
    """Ответ пришёл, но в нём нет пригодного текста."""


@dataclass
class CallResult:
    """Результат одного вызова модели."""

    text: str
    model: str
    tokens: Dict[str, int] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)
    stop_reason: Optional[str] = None


def extract_text(payload: Dict[str, Any]) -> str:
    """Собрать текст из блоков ответа, отбирая их по ``type == "text"``.

    У MiniMax в ``content`` первым идёт thinking-блок, поэтому обращение к
    ``content[0]["text"]`` даёт KeyError или чужой текст.
    """

    content = payload.get("content")
    if isinstance(content, str):
        return content.strip()
    chunks: List[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    return "\n".join(chunks).strip()


def extract_tokens(payload: Dict[str, Any]) -> Dict[str, int]:
    """Вытащить usage, если провайдер его вернул."""

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}
    tokens: Dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            tokens[key] = int(value)
    if "total_tokens" not in tokens and tokens:
        tokens["total_tokens"] = tokens.get("input_tokens", 0) + tokens.get("output_tokens", 0)
    return tokens


def post_json(
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout: float,
    *,
    opener: Optional[Callable[[urllib.request.Request, float], Any]] = None,
) -> Dict[str, Any]:
    """POST JSON через ``urllib.request`` (никаких ``requests``).

    ``opener`` существует только для тестов: по умолчанию используется
    ``urllib.request.urlopen``.
    """

    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    for name, value in headers.items():
        request.add_header(name, value)

    call = opener or (lambda req, tmo: urllib.request.urlopen(req, timeout=tmo))
    try:
        with call(request, timeout) as response:  # type: ignore[arg-type]
            raw = response.read()
    except urllib.error.HTTPError as exc:  # noqa: PERF203 - ветки разные по смыслу
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:  # pragma: no cover - поток уже закрыт
            detail = ""
        raise TransportHTTPError(exc.code, detail, url) from exc
    except urllib.error.URLError as exc:
        raise TransportNetworkError(f"сеть недоступна: {exc.reason}") from exc
    except TimeoutError as exc:
        raise TransportNetworkError(f"таймаут {timeout}s при запросе к {url}") from exc

    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TransportPayloadError(f"ответ не является JSON: {text[:200]}") from exc
    if not isinstance(decoded, dict):
        raise TransportPayloadError("ожидался JSON-объект в ответе")
    return decoded


class MiniMaxTransport:
    """Тонкий клиент anthropic-совместимого MiniMax endpoint."""

    def __init__(
        self,
        config: Optional[RouterConfig] = None,
        token: Optional[str] = None,
        *,
        opener: Optional[Callable[[urllib.request.Request, float], Any]] = None,
        token_required: bool = True,
    ) -> None:
        self.config = config or RouterConfig()
        self._token = token
        self._token_required = token_required
        self._opener = opener

    # --- auth ---

    @property
    def token(self) -> Optional[str]:
        """Токен подтягивается лениво, чтобы импорт модуля не требовал auth."""

        if self._token is None:
            self._token = resolve_access_token(self.config, required=self._token_required)
        return self._token

    def headers(self) -> Dict[str, str]:
        token = self.token or ""
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "anthropic-version": self.config.anthropic_version,
            "x-api-key": token,
            "Authorization": f"Bearer {token}",
        }

    # --- вызовы ---

    def build_payload(
        self,
        content: Sequence[Dict[str, Any]] | str,
        *,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        max_tokens: Optional[int] = None,
        system: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Собрать тело запроса в формате ``/anthropic/v1/messages``."""

        message_content: Any
        if isinstance(content, str):
            message_content = [{"type": "text", "text": content}]
        else:
            message_content = list(content)

        payload: Dict[str, Any] = {
            "model": model or self.config.default_model,
            "max_tokens": int(max_tokens or self.config.max_tokens_for_effort(effort)),
            "messages": [{"role": "user", "content": message_content}],
        }
        if system:
            payload["system"] = system
        return payload

    def request(self, payload: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        return post_json(
            self.config.api_url,
            payload,
            self.headers(),
            float(timeout or self.config.request_timeout),
            opener=self._opener,
        )

    def call(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        max_tokens: Optional[int] = None,
        system: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> CallResult:
        """Один текстовый вызов модели."""

        payload = self.build_payload(
            prompt, model=model, effort=effort, max_tokens=max_tokens, system=system
        )
        decoded = self.request(payload, timeout)
        text = extract_text(decoded)
        if not text:
            raise TransportPayloadError(
                f"модель {payload['model']} вернула ответ без текстовых блоков "
                f"(stop_reason={decoded.get('stop_reason')!r})"
            )
        return CallResult(
            text=text,
            model=str(decoded.get("model") or payload["model"]),
            tokens=extract_tokens(decoded),
            raw=decoded,
            stop_reason=decoded.get("stop_reason"),
        )


def _prompt_from_payload(payload: Dict[str, Any]) -> str:
    """Собрать текстовую часть запроса — для стабов и логов."""

    chunks: List[str] = []
    for message in payload.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    chunks.append(str(block.get("text") or ""))
    return "\n".join(chunk for chunk in chunks if chunk)


class StubTransport:
    """Оффлайн-транспорт для тестов и ``--selftest``: в сеть не ходит.

    ``responses`` — либо callable ``(prompt, model) -> str``, либо словарь
    «подстрока промпта -> ответ», либо список ответов по порядку вызовов.
    """

    def __init__(
        self,
        responses: Any = None,
        *,
        config: Optional[RouterConfig] = None,
        default: str = "stub response",
        latency_sec: float = 0.0,
    ) -> None:
        self.config = config or RouterConfig()
        self.responses = responses
        self.default = default
        self.latency_sec = latency_sec
        self.calls: List[Dict[str, Any]] = []

    def call(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        max_tokens: Optional[int] = None,
        system: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> CallResult:
        model_name = model or self.config.default_model
        self.calls.append({"prompt": prompt, "model": model_name, "effort": effort})
        if self.latency_sec:
            import time

            time.sleep(self.latency_sec)
        text = self._resolve(prompt, model_name)
        if isinstance(text, BaseException):
            raise text
        return CallResult(
            text=text,
            model=model_name,
            tokens={"input_tokens": len(prompt.split()), "output_tokens": len(text.split())},
            raw={"stub": True},
            stop_reason="end_turn",
        )

    def request(self, payload: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        """Низкоуровневый ответ в формате API — нужен vision-слою."""

        model_name = str(payload.get("model") or self.config.default_model)
        prompt = _prompt_from_payload(payload)
        self.calls.append({"prompt": prompt, "model": model_name, "payload": payload})
        text = self._resolve(prompt, model_name)
        if isinstance(text, BaseException):
            raise text
        return {
            "model": model_name,
            "stop_reason": "end_turn",
            # Первый блок намеренно не текстовый: так же ведёт себя thinking-модель.
            "content": [
                {"type": "thinking", "thinking": "stub reasoning"},
                {"type": "text", "text": text},
            ],
            "usage": {"input_tokens": len(prompt.split()), "output_tokens": len(str(text).split())},
        }

    def _resolve(self, prompt: str, model: str) -> Any:
        responses = self.responses
        if responses is None:
            return self.default
        if isinstance(responses, BaseException):
            return responses
        if callable(responses):
            return responses(prompt, model)
        if isinstance(responses, dict):
            for needle, value in responses.items():
                if needle in prompt:
                    return value
            return self.default
        if isinstance(responses, list):
            index = len(self.calls) - 1
            if index < len(responses):
                return responses[index]
            return responses[-1] if responses else self.default
        return str(responses)
