"""Транспорт: заголовки, парсинг ответа и классификация HTTP-ошибок."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List

import pytest

from src.llm_router.config import RouterConfig
from src.llm_router.transport import (
    MiniMaxTransport,
    TransportHTTPError,
    TransportNetworkError,
    TransportPayloadError,
    extract_text,
    extract_tokens,
    post_json,
)


class FakeResponse(io.BytesIO):
    """Минимальный контекстный менеджер вместо http.client.HTTPResponse."""

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


def make_opener(payload: Dict[str, Any], sink: List[urllib.request.Request]):
    def opener(request: urllib.request.Request, timeout: float) -> FakeResponse:
        sink.append(request)
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    return opener


def test_extract_text_skips_non_text_blocks() -> None:
    payload = {
        "content": [
            {"type": "thinking", "thinking": "рассуждения, которые не нужны"},
            {"type": "text", "text": "первая часть"},
            {"type": "text", "text": "вторая часть"},
        ]
    }

    assert extract_text(payload) == "первая часть\nвторая часть"
    assert extract_text({"content": []}) == ""
    assert extract_text({"content": "строкой"}) == "строкой"


def test_extract_tokens_fills_total() -> None:
    tokens = extract_tokens({"usage": {"input_tokens": 100, "output_tokens": 20}})

    assert tokens == {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    assert extract_tokens({}) == {}


def test_call_builds_anthropic_payload_and_headers() -> None:
    requests: List[urllib.request.Request] = []
    config = RouterConfig()
    transport = MiniMaxTransport(
        config,
        token="secret-token",
        opener=make_opener(
            {
                "model": "MiniMax-M3.1-Flash",
                "content": [
                    {"type": "thinking", "thinking": "..."},
                    {"type": "text", "text": "готово"},
                ],
                "usage": {"input_tokens": 5, "output_tokens": 2},
                "stop_reason": "end_turn",
            },
            requests,
        ),
    )

    result = transport.call("вопрос", effort="low")

    assert result.text == "готово"
    assert result.tokens["total_tokens"] == 7
    assert len(requests) == 1
    request = requests[0]
    assert request.full_url == "https://api.minimax.io/anthropic/v1/messages"
    assert request.get_header("Anthropic-version") == "2023-06-01"
    assert request.get_header("X-api-key") == "secret-token"
    body = json.loads(request.data.decode("utf-8"))
    assert body["model"] == "MiniMax-M3.1-Flash"
    assert body["max_tokens"] == config.effort_profiles["low"]["max_tokens"]
    assert body["messages"][0]["content"][0] == {"type": "text", "text": "вопрос"}


def test_answer_without_text_block_raises() -> None:
    transport = MiniMaxTransport(
        RouterConfig(),
        token="t",
        opener=make_opener({"content": [{"type": "thinking", "thinking": "..."}]}, []),
    )

    with pytest.raises(TransportPayloadError):
        transport.call("вопрос")


def test_http_error_maps_to_transport_error_with_retry_flag() -> None:
    def opener(request: urllib.request.Request, timeout: float) -> FakeResponse:
        raise urllib.error.HTTPError(
            request.full_url, 429, "Too Many Requests", {}, io.BytesIO(b"slow down")
        )

    with pytest.raises(TransportHTTPError) as excinfo:
        post_json("https://example.invalid", {}, {}, 1.0, opener=opener)

    assert excinfo.value.status_code == 429
    assert excinfo.value.retryable is True
    assert TransportHTTPError(451, "legal").retryable is False
    assert TransportHTTPError(400, "bad").retryable is False


def test_url_error_maps_to_network_error() -> None:
    def opener(request: urllib.request.Request, timeout: float) -> FakeResponse:
        raise urllib.error.URLError("name resolution failed")

    with pytest.raises(TransportNetworkError):
        post_json("https://example.invalid", {}, {}, 1.0, opener=opener)


def test_non_json_body_raises_payload_error() -> None:
    def opener(request: urllib.request.Request, timeout: float) -> FakeResponse:
        return FakeResponse(b"<html>502</html>")

    with pytest.raises(TransportPayloadError):
        post_json("https://example.invalid", {}, {}, 1.0, opener=opener)
