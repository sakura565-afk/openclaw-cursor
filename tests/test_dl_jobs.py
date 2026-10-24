"""Tests for scripts.dl_jobs using a local HTTP server."""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scripts.dl_jobs import run_jobs

PAYLOAD_A = b"job-a-" + (b"a" * 2000)
PAYLOAD_B = b"job-b-" + (b"b" * 3000)
PAYLOAD_C = b"job-c-" + (b"c" * 2500)

FILES = {
    "/a.bin": PAYLOAD_A,
    "/b.bin": PAYLOAD_B,
    "/c.bin": PAYLOAD_C,
}


class MultiFileHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def do_HEAD(self) -> None:
        path = self.path.split("?", 1)[0]
        data = FILES.get(path)
        if data is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        data = FILES.get(path)
        if data is None:
            self.send_error(404)
            return
        rng = self.headers.get("Range")
        if rng:
            start_s, end_s = rng.replace("bytes=", "").split("-", 1)
            start = int(start_s)
            end = int(end_s) if end_s else len(data) - 1
            chunk = data[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Length", str(len(chunk)))
            self.send_header(
                "Content-Range", f"bytes {start}-{start + len(chunk) - 1}/{len(data)}"
            )
            self.end_headers()
            self.wfile.write(chunk)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def jobs_server() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), MultiFileHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    try:
        yield base
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_run_jobs_parallel_and_skip_done(jobs_server: str, tmp_path: Path) -> None:
    dest_a = tmp_path / "a.safetensors"
    dest_b = tmp_path / "b.safetensors"
    dest_c = tmp_path / "c.safetensors"
    state_path = tmp_path / "state.json"

    jobs = [
        {
            "id": "a",
            "url": f"{jobs_server}/a.bin",
            "dest": str(dest_a),
            "expect_size": len(PAYLOAD_A),
            "sha256": _sha(PAYLOAD_A),
        },
        {
            "id": "b",
            "url": f"{jobs_server}/b.bin",
            "dest": str(dest_b),
            "expect_size": len(PAYLOAD_B),
            "sha256": _sha(PAYLOAD_B),
        },
        {
            "id": "c",
            "url": f"{jobs_server}/c.bin",
            "dest": str(dest_c),
            "expect_size": len(PAYLOAD_C),
            "sha256": _sha(PAYLOAD_C),
        },
    ]

    counts = run_jobs(jobs, workers=2, state_path=str(state_path))
    assert counts == {"ok": 3, "failed": 0, "skipped": 0}
    assert dest_a.read_bytes() == PAYLOAD_A
    assert dest_b.read_bytes() == PAYLOAD_B
    assert dest_c.read_bytes() == PAYLOAD_C

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["a"]["status"] == "done"
    assert state["b"]["status"] == "done"
    assert state["c"]["status"] == "done"

    counts2 = run_jobs(jobs, workers=2, state_path=str(state_path))
    assert counts2 == {"ok": 0, "failed": 0, "skipped": 3}
