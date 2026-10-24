"""Tests for scripts.dl_verified using a local HTTP server."""

from __future__ import annotations

import hashlib
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scripts.dl_verified import DownloadVerificationError, download, head_size


PAYLOAD = b"verified-download-payload-" + (b"x" * 4096)
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
PAYLOAD_LEN = len(PAYLOAD)


class DownloadState:
    """Mutable server behavior shared across requests."""

    def __init__(self) -> None:
        self.truncate_to: int | None = None
        self.truncate_first_n_gets: int = 0
        self.ignore_range = False
        self.get_attempt = 0
        self.range_headers: list[str | None] = []
        self.lock = threading.Lock()


STATE = DownloadState()


class FileHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(PAYLOAD_LEN))
        self.end_headers()

    def do_GET(self) -> None:
        with STATE.lock:
            STATE.get_attempt += 1
            attempt = STATE.get_attempt
            rng = self.headers.get("Range")
            STATE.range_headers.append(rng)

            body = PAYLOAD
            if STATE.truncate_first_n_gets and attempt <= STATE.truncate_first_n_gets:
                body = PAYLOAD[:100]
            elif STATE.truncate_to is not None:
                body = PAYLOAD[: STATE.truncate_to]

            if STATE.ignore_range:
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if rng:
                start_s, end_s = rng.replace("bytes=", "").split("-", 1)
                start = int(start_s)
                end = int(end_s) if end_s else PAYLOAD_LEN - 1
                chunk = body[start : end + 1]
                self.send_response(206)
                self.send_header("Content-Length", str(len(chunk)))
                self.send_header(
                    "Content-Range", f"bytes {start}-{start + len(chunk) - 1}/{PAYLOAD_LEN}"
                )
                self.end_headers()
                self.wfile.write(chunk)
                return

            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


@pytest.fixture
def http_server() -> str:
    STATE.truncate_to = None
    STATE.truncate_first_n_gets = 0
    STATE.ignore_range = False
    STATE.get_attempt = 0
    STATE.range_headers = []

    server = ThreadingHTTPServer(("127.0.0.1", 0), FileHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}/file.bin"
    try:
        yield base
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_head_size(http_server: str) -> None:
    assert head_size(http_server) == PAYLOAD_LEN


def test_download_success(http_server: str, tmp_path: Path) -> None:
    dest = tmp_path / "model.safetensors"
    result = download(
        http_server,
        str(dest),
        expected_size=PAYLOAD_LEN,
        sha256=PAYLOAD_SHA,
        max_retries=3,
    )
    assert dest.read_bytes() == PAYLOAD
    assert not (tmp_path / "model.safetensors.part").exists()
    assert result["bytes"] == PAYLOAD_LEN
    assert result["sha256"] == PAYLOAD_SHA
    assert result["attempts"] == 1


def test_truncated_then_full(http_server: str, tmp_path: Path) -> None:
    STATE.truncate_first_n_gets = 1
    dest = tmp_path / "trunc.safetensors"
    result = download(
        http_server,
        str(dest),
        expected_size=PAYLOAD_LEN,
        sha256=PAYLOAD_SHA,
        max_retries=5,
    )
    assert dest.read_bytes() == PAYLOAD
    assert result["attempts"] >= 2


def test_truncated_raises_without_recovery(http_server: str, tmp_path: Path) -> None:
    STATE.truncate_to = 50
    dest = tmp_path / "bad.safetensors"
    with pytest.raises(DownloadVerificationError):
        download(
            http_server,
            str(dest),
            expected_size=PAYLOAD_LEN,
            max_retries=2,
        )
    assert not dest.exists()


def test_resume_sends_range(http_server: str, tmp_path: Path) -> None:
    STATE.range_headers.clear()
    dest = tmp_path / "resume.bin"
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(PAYLOAD[:500])

    result = download(
        http_server,
        str(dest),
        expected_size=PAYLOAD_LEN,
        sha256=PAYLOAD_SHA,
        resume=True,
        max_retries=3,
    )
    assert dest.read_bytes() == PAYLOAD
    assert any(h and h.startswith("bytes=500-") for h in STATE.range_headers)
    assert result["resumed"] is True


def test_range_ignored_restarts_cleanly(http_server: str, tmp_path: Path) -> None:
    STATE.ignore_range = True
    dest = tmp_path / "noresize.bin"
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(b"partial-stale-data")

    download(
        http_server,
        str(dest),
        expected_size=PAYLOAD_LEN,
        sha256=PAYLOAD_SHA,
        resume=True,
        max_retries=3,
    )
    assert dest.read_bytes() == PAYLOAD
