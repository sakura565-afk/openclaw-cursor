"""Vision-слой: прямой вызов ``MiniMax-Vision-32B`` и 451 как штатный фолбэк.

Почему не через ``vision_analyze``: внешний хелпер отдаёт HTTP 451 на части
контента. Это не транзиентная ошибка — ретраить тот же путь бессмысленно,
нужно переключаться на прямой вызов ``/anthropic/v1/messages``.

Правила прямого вызова:

* модель ``MiniMax-Vision-32B``;
* картинка в ``messages[0].content[0].source.data`` (base64);
* ``max_tokens = 6000`` — thinking съедает бюджет, и при меньшем лимите
  текстового блока в ответе просто нет;
* ответ парсится перебором блоков по ``type == "text"``.
"""

from __future__ import annotations

import base64
from typing import Any, Callable, Dict, Optional

from src.llm_router.config import RouterConfig
from src.llm_router.transport import (
    MiniMaxTransport,
    TransportError,
    VISION_BLOCKED_STATUS,
    extract_text,
    extract_tokens,
)

#: Подстроки, по которым 451 узнаётся даже без кода статуса.
BLOCKED_MARKERS = ("unavailable for legal reasons", "451", "content_policy", "nsfw")

DEFAULT_MEDIA_TYPE = "image/jpeg"
_MAGIC_PREFIXES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class VisionError(RuntimeError):
    """Vision-путь не дал текста. Структурированная ошибка вместо пустого ответа."""

    def __init__(
        self, message: str, *, stage: str = "direct", cause: Optional[BaseException] = None
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.cause = cause

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": str(self),
            "stage": self.stage,
            "cause": None if self.cause is None else f"{type(self.cause).__name__}: {self.cause}",
        }


def is_vision_blocked(exc: BaseException) -> bool:
    """Это 451 (контент заблокирован), а не обычный сбой?"""

    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status == VISION_BLOCKED_STATUS:
        return True
    code = getattr(exc, "code", None)
    if isinstance(code, int) and code == VISION_BLOCKED_STATUS:
        return True
    haystack = f"{exc} {getattr(exc, 'body', '')}".lower()
    return any(marker in haystack for marker in BLOCKED_MARKERS)


def detect_media_type(image_bytes: bytes) -> str:
    """Определить media type по магическим байтам (webp — через RIFF)."""

    head = bytes(image_bytes[:16])
    for prefix, media_type in _MAGIC_PREFIXES:
        if head.startswith(prefix):
            return media_type
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return DEFAULT_MEDIA_TYPE


def build_vision_payload(
    image_bytes: bytes,
    prompt: str,
    config: Optional[RouterConfig] = None,
    *,
    media_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Собрать тело запроса: картинка первым блоком, текст — вторым."""

    config = config or RouterConfig()
    if not image_bytes:
        raise VisionError("пустые байты изображения", stage="payload")
    encoded = base64.b64encode(bytes(image_bytes)).decode("ascii")
    return {
        "model": config.vision_model,
        "max_tokens": min(int(config.vision_max_tokens), 6000),
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type or detect_media_type(image_bytes),
                            "data": encoded,
                        },
                    },
                    {"type": "text", "text": prompt or "Describe this image."},
                ],
            }
        ],
    }


def _call_direct(
    image_bytes: bytes,
    prompt: str,
    config: RouterConfig,
    transport: Any,
    *,
    stage: str,
) -> str:
    payload = build_vision_payload(image_bytes, prompt, config)
    try:
        decoded = transport.request(payload, config.vision_timeout)
    except TransportError as exc:
        raise VisionError(
            f"прямой vision-вызов не удался: {exc}", stage=stage, cause=exc
        ) from exc
    text = extract_text(decoded)
    if not text:
        raise VisionError(
            "vision-модель не вернула текстовых блоков "
            f"(stop_reason={decoded.get('stop_reason')!r}, tokens={extract_tokens(decoded)})",
            stage=stage,
        )
    return text


def vision_fallback(
    image_bytes: bytes,
    prompt: str,
    config: Optional[RouterConfig] = None,
    *,
    transport: Any = None,
) -> str:
    """Обход заблокированного пути: прямой вызов MiniMax.

    При недоступности поднимает :class:`VisionError` — молчаливого пустого
    ответа здесь быть не должно.
    """

    config = config or RouterConfig()
    transport = transport if transport is not None else MiniMaxTransport(config)
    return _call_direct(image_bytes, prompt, config, transport, stage="fallback")


def analyze_image(
    image_bytes: bytes,
    prompt: str,
    config: Optional[RouterConfig] = None,
    *,
    transport: Any = None,
    primary: Optional[Callable[[bytes, str], str]] = None,
) -> str:
    """Разобрать картинку. ``primary`` — внешний хелпер (``vision_analyze``).

    Если ``primary`` задан и упал с 451 — это ожидаемая ветка: уходим в
    :func:`vision_fallback`, не ретрая тот же путь. Без ``primary`` сразу
    идём прямым вызовом.
    """

    config = config or RouterConfig()
    transport = transport if transport is not None else MiniMaxTransport(config)

    if primary is not None:
        try:
            text = primary(image_bytes, prompt)
        except Exception as exc:  # noqa: BLE001 - решение принимаем по типу ошибки
            if not is_vision_blocked(exc):
                raise VisionError(
                    f"основной vision-путь упал: {exc}", stage="primary", cause=exc
                ) from exc
            return vision_fallback(image_bytes, prompt, config, transport=transport)
        if text and text.strip():
            return text.strip()
        return vision_fallback(image_bytes, prompt, config, transport=transport)

    return _call_direct(image_bytes, prompt, config, transport, stage="direct")
