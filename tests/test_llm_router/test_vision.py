"""Vision: прямой вызов MiniMax-Vision-32B, парсинг по type=="text", 451 как фолбэк."""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

import pytest

from src.llm_router.config import RouterConfig
from src.llm_router.transport import TransportHTTPError, TransportNetworkError
from src.llm_router.vision import (
    VisionError,
    analyze_image,
    build_vision_payload,
    detect_media_type,
    is_vision_blocked,
    vision_fallback,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes"


class RecordingTransport:
    """Собирает payload-ы и отдаёт заранее заданный ответ API."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.payloads: List[Dict[str, Any]] = []

    def request(self, payload: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        self.payloads.append(payload)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def api_response(text: Optional[str]) -> Dict[str, Any]:
    """Ответ vision-модели: thinking-блок первым, текст — вторым."""

    content: List[Dict[str, Any]] = [{"type": "thinking", "thinking": "смотрю на картинку"}]
    if text is not None:
        content.append({"type": "text", "text": text})
    return {
        "model": "MiniMax-Vision-32B",
        "stop_reason": "end_turn",
        "content": content,
        "usage": {"input_tokens": 1200, "output_tokens": 300},
    }


def test_is_vision_blocked_recognizes_451() -> None:
    assert is_vision_blocked(TransportHTTPError(451, "Unavailable For Legal Reasons")) is True
    assert is_vision_blocked(RuntimeError("vision_analyze failed: HTTP 451")) is True
    assert is_vision_blocked(TransportHTTPError(500, "oops")) is False
    assert is_vision_blocked(TransportNetworkError("нет сети")) is False


def test_payload_follows_direct_minimax_contract() -> None:
    config = RouterConfig()
    payload = build_vision_payload(PNG_BYTES, "Что на картинке?", config)

    assert payload["model"] == "MiniMax-Vision-32B"
    assert payload["max_tokens"] == 6000
    source = payload["messages"][0]["content"][0]["source"]
    assert source["type"] == "base64"
    assert source["media_type"] == "image/png"
    assert base64.b64decode(source["data"]) == PNG_BYTES
    assert payload["messages"][0]["content"][1] == {"type": "text", "text": "Что на картинке?"}


def test_vision_max_tokens_capped_at_6000() -> None:
    assert build_vision_payload(PNG_BYTES, "x", RouterConfig(vision_max_tokens=99999))[
        "max_tokens"
    ] == 6000


def test_media_type_detection() -> None:
    assert detect_media_type(PNG_BYTES) == "image/png"
    assert detect_media_type(b"\xff\xd8\xff\xe0jfif") == "image/jpeg"
    assert detect_media_type(b"RIFF1234WEBPVP8 ") == "image/webp"
    assert detect_media_type(b"unknown-format") == "image/jpeg"


def test_analyze_image_parses_text_block() -> None:
    transport = RecordingTransport(api_response("На картинке стул у окна."))

    text = analyze_image(PNG_BYTES, "Что на картинке?", RouterConfig(), transport=transport)

    assert text == "На картинке стул у окна."
    assert len(transport.payloads) == 1


def test_blocked_primary_path_triggers_fallback() -> None:
    transport = RecordingTransport(api_response("ответ прямого вызова"))
    primary_calls: List[str] = []

    def blocked_primary(image: bytes, prompt: str) -> str:
        primary_calls.append(prompt)
        raise TransportHTTPError(451, "Unavailable For Legal Reasons")

    text = analyze_image(
        PNG_BYTES, "Опиши", RouterConfig(), transport=transport, primary=blocked_primary
    )

    assert primary_calls == ["Опиши"]  # повторного обращения к тому же пути нет
    assert text == "ответ прямого вызова"
    assert transport.payloads[0]["model"] == "MiniMax-Vision-32B"


def test_non_451_primary_failure_is_not_silently_swallowed() -> None:
    transport = RecordingTransport(api_response("не должен вызываться"))

    def failing_primary(image: bytes, prompt: str) -> str:
        raise TransportHTTPError(500, "upstream")

    with pytest.raises(VisionError) as excinfo:
        analyze_image(
            PNG_BYTES, "Опиши", RouterConfig(), transport=transport, primary=failing_primary
        )

    assert excinfo.value.stage == "primary"
    assert transport.payloads == []


def test_empty_primary_answer_falls_back() -> None:
    transport = RecordingTransport(api_response("прямой ответ"))

    text = analyze_image(
        PNG_BYTES,
        "Опиши",
        RouterConfig(),
        transport=transport,
        primary=lambda image, prompt: "   ",
    )

    assert text == "прямой ответ"


def test_missing_text_block_raises_structured_error() -> None:
    transport = RecordingTransport(api_response(None))

    with pytest.raises(VisionError) as excinfo:
        vision_fallback(PNG_BYTES, "Опиши", RouterConfig(), transport=transport)

    payload = excinfo.value.to_dict()
    assert payload["stage"] == "fallback"
    assert "текстовых блоков" in payload["error"]


def test_transport_error_wrapped_into_vision_error() -> None:
    transport = RecordingTransport(TransportNetworkError("нет сети"))

    with pytest.raises(VisionError):
        vision_fallback(PNG_BYTES, "Опиши", RouterConfig(), transport=transport)


def test_empty_image_rejected() -> None:
    with pytest.raises(VisionError):
        build_vision_payload(b"", "Опиши", RouterConfig())
