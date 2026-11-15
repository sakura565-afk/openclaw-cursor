from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import tools.hf_download as hf
from tools.hf_download import Downloader, FileTask


class RangeServer:
    def __init__(
        self, payload: bytes, fail_after: int | None = None,
        corrupt_start: int | None = None, delay: float = 0,
        oid: str | None = None,
    ):
        self.payload = payload
        self.fail_after = fail_after
        self.corrupt_start = corrupt_start
        self.delay = delay
        self.oid = oid
        self.requests = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/api/"):
                    body = json.dumps([{
                        "path": "model.bin", "lfs": {"oid": server.oid},
                    }]).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                start, end = self._range()
                server.requests.append((start, end))
                if server.delay:
                    time.sleep(server.delay)
                self.send_response(206)
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                if server.fail_after is not None:
                    self.wfile.write(server.payload[start : start + server.fail_after])
                    self.close_connection = True
                    return
                chunk = bytearray(server.payload[start : end + 1])
                if server.corrupt_start == start:
                    chunk[0] ^= 0xFF
                self.wfile.write(chunk)

            def _range(self):
                value = self.headers["Range"]
                start, end = value.removeprefix("bytes=").split("-", 1)
                return int(start), int(end)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=1)


@pytest.fixture
def range_server():
    servers = []

    def make(payload, **kwargs):
        server = RangeServer(payload, **kwargs)
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.close()


def test_clean_download_has_exact_size_and_sha256(tmp_path, range_server):
    payload = bytes(range(256)) * 32
    server = range_server(payload)
    dest = tmp_path / "model.bin"
    task = FileTask(
        repo="org/repo",
        path="model.bin",
        dest=str(dest),
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    result = Downloader(base_url=server.url).download(task, segments=4)

    assert result.status == "ok"
    assert result.size == len(payload)
    assert result.sha256_match is True
    assert dest.read_bytes() == payload


def test_wrong_size_on_disk_is_not_treated_as_complete(tmp_path, range_server):
    payload = bytes(range(256)) * 32
    server = range_server(payload)
    dest = tmp_path / "model.bin"
    dest.write_bytes(payload[:64])
    task = FileTask(
        repo="org/repo",
        path="model.bin",
        dest=str(dest),
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    result = Downloader(base_url=server.url).download(task, segments=4)

    assert result.status == "ok"
    assert dest.stat().st_size == len(payload)
    assert dest.read_bytes() == payload


def test_interrupted_segment_resumes_on_second_run(tmp_path, range_server):
    payload = bytes(reversed(range(256))) * 32
    server = range_server(payload, fail_after=100)
    dest = tmp_path / "model.bin"
    task = FileTask(
        repo="org/repo", path="model.bin", dest=str(dest), size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    downloader = Downloader(base_url=server.url, max_retries=0)

    interrupted = downloader.download(task, segments=4)
    assert interrupted.status == "download_failed"
    assert (tmp_path / "model.bin.parts" / "p000").stat().st_size == 100

    server.fail_after = None
    result = downloader.download(task, segments=4)

    assert result.status == "ok"
    assert dest.read_bytes() == payload
    assert (100, 2047) in server.requests


def test_corrupted_segment_is_never_reported_ok(tmp_path, range_server):
    payload = bytes(range(256)) * 32
    server = range_server(payload, corrupt_start=2048)
    dest = tmp_path / "model.bin"
    task = FileTask(
        repo="org/repo", path="model.bin", dest=str(dest), size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    result = Downloader(base_url=server.url).download(task, segments=4)

    assert result.status == "checksum_mismatch"
    assert result.sha256_match is False
    assert result.sha256 != task.sha256
    assert result.status != "ok"
    assert not dest.exists()


def test_different_existing_segment_layout_is_preserved(tmp_path, range_server):
    payload = b"z" * 8192
    server = range_server(payload)
    dest = tmp_path / "model.bin"
    parts = tmp_path / "model.bin.parts"
    parts.mkdir()
    (parts / "p000").write_bytes(b"keep-zero")
    (parts / "p001").write_bytes(b"keep-one")
    task = FileTask(
        repo="org/repo", path="model.bin", dest=str(dest), size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    result = Downloader(base_url=server.url).download(task, segments=4)

    assert result.status == "segment_layout_mismatch"
    assert (parts / "p000").read_bytes() == b"keep-zero"
    assert (parts / "p001").read_bytes() == b"keep-one"
    assert not dest.exists()
    assert server.requests == []


def test_empty_sha256_is_discovered_from_hf_lfs_oid(tmp_path, range_server):
    payload = b"metadata-hash" * 300
    expected = hashlib.sha256(payload).hexdigest()
    server = range_server(payload, oid=f"sha256:{expected}")
    dest = tmp_path / "model.bin"
    task = FileTask(
        repo="org/repo", path="model.bin", dest=str(dest), size=len(payload), sha256="",
    )

    result = Downloader(base_url=server.url).download(task, segments=3)

    assert result.status == "ok"
    assert result.sha256 == expected
    assert result.sha256_match is True
    assert dest.read_bytes() == payload


def test_two_processes_share_lock_without_duplicate_segment_writes(tmp_path, range_server):
    payload = bytes(range(256)) * 2048
    server = range_server(payload, delay=0.05)
    dest = tmp_path / "model.bin"
    task = FileTask(
        repo="org/repo", path="model.bin", dest=str(dest), size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    repo = Path(__file__).resolve().parents[1]
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import json, sys\n"
        "from dataclasses import asdict\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(repo)!r})\n"
        "from tools.hf_download import Downloader, FileTask\n"
        "config = json.loads(Path(sys.argv[1]).read_text())\n"
        "result = Downloader(base_url=config['url']).download(FileTask(**config['task']), segments=4)\n"
        "Path(config['result']).write_text(json.dumps(asdict(result)))\n",
        encoding="utf-8",
    )

    processes = []
    results = []
    for index in range(2):
        result_path = tmp_path / f"result-{index}.json"
        config = tmp_path / f"config-{index}.json"
        config.write_text(json.dumps({
            "url": server.url, "task": asdict(task), "result": str(result_path),
        }))
        processes.append(subprocess.Popen(
            [sys.executable, str(worker), str(config)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ))
        results.append(result_path)
        if index == 0:
            deadline = time.monotonic() + 1
            while not server.requests and time.monotonic() < deadline:
                time.sleep(0.005)

    for process in processes:
        stdout, stderr = process.communicate(timeout=4)
        assert process.returncode == 0, stderr.decode()
    for result_path in results:
        assert json.loads(result_path.read_text())["status"] == "ok"
    assert dest.stat().st_size == len(payload)
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == task.sha256
    assert sorted(server.requests) == [
        (0, 131071), (131072, 262143), (262144, 393215), (393216, 524287),
    ]


def test_plan_builds_file_tasks():
    spec = {"files": [{
        "repo": "org/repo", "path": "model.bin", "dest": "out/model.bin",
        "size": 123, "sha256": "abc",
    }]}

    tasks = Downloader().plan(spec)

    assert tasks == [FileTask("org/repo", "model.bin", "out/model.bin", 123, "abc")]


def test_cli_dry_run_filters_without_downloading(tmp_path, capsys):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"files": [
        {"repo": "org/first", "path": "a.bin", "dest": "a.bin", "size": 1, "sha256": "a"},
        {"repo": "org/other", "path": "b.bin", "dest": "b.bin", "size": 1, "sha256": "b"},
    ]}))
    report = tmp_path / "report.json"

    exit_code = hf.main([
        "--plan", str(plan), "--only", "first", "--dry-run", "--report", str(report),
    ])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)[0]["repo"] == "org/first"
    assert not report.exists()


def test_cli_writes_one_json_report_entry_per_file(tmp_path, range_server, monkeypatch):
    payload = b"report" * 100
    server = range_server(payload)
    monkeypatch.setenv("HF_ENDPOINT", server.url)
    dest = tmp_path / "model.bin"
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"files": [{
        "repo": "org/repo", "path": "model.bin", "dest": str(dest),
        "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
    }]}))
    report = tmp_path / "report.json"

    exit_code = hf.main(["--plan", str(plan), "--report", str(report)])

    assert exit_code == 0
    entries = json.loads(report.read_text())
    assert len(entries) == 1
    assert entries[0]["file"] == str(dest)
    assert entries[0]["status"] == "ok"
    assert entries[0]["sha256_match"] is True


def test_stalled_connection_is_aborted(tmp_path, range_server):
    payload = b"slow" * 1000
    server = range_server(payload, delay=0.05)
    task = FileTask(
        repo="org/repo", path="model.bin", dest=str(tmp_path / "model.bin"),
        size=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
    )
    downloader = Downloader(
        base_url=server.url, max_retries=0,
        speed_limit=1024 * 1024 * 1024, speed_time=0.01,
    )

    result = downloader.download(task, segments=1)

    assert result.status == "download_failed"
    assert result.sha256_match is False


def test_empty_file_needs_no_http_requests(tmp_path, range_server):
    server = range_server(b"")
    dest = tmp_path / "empty.bin"
    task = FileTask(
        repo="org/repo", path="empty.bin", dest=str(dest), size=0,
        sha256=hashlib.sha256(b"").hexdigest(),
    )

    result = Downloader(base_url=server.url).download(task, segments=3)

    assert result.status == "ok"
    assert result.size == 0
    assert dest.read_bytes() == b""
    assert server.requests == []
