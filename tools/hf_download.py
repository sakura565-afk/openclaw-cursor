from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote


@dataclass(frozen=True)
class FileTask:
    repo: str
    path: str
    dest: str
    size: int
    sha256: str


@dataclass
class DownloadResult:
    file: str
    status: str
    size: int
    sha256: str
    sha256_match: bool
    seconds: float
    segments: int


class Downloader:
    def __init__(
        self, base_url: str = "https://huggingface.co", max_retries: int = 3,
        backoff: float = 0.25, lock_timeout: float = 120,
        speed_limit: int = 200 * 1024, speed_time: float = 60,
    ):
        self.base_url = os.environ.get("HF_ENDPOINT", base_url).rstrip("/")
        self.max_retries = max_retries
        self.backoff = backoff
        self.lock_timeout = lock_timeout
        self.speed_limit = speed_limit
        self.speed_time = speed_time

    def plan(self, spec: dict) -> list[FileTask]:
        return [
            FileTask(
                repo=item["repo"], path=item["path"], dest=item["dest"],
                size=int(item["size"]), sha256=item.get("sha256", ""),
            )
            for item in spec["files"]
        ]

    def download(self, task: FileTask, segments: int = 6) -> DownloadResult:
        if segments < 1:
            raise ValueError("segments must be positive")
        if task.size < 0:
            raise ValueError("file size cannot be negative")
        started = time.monotonic()
        dest = Path(task.dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._destination_lock(dest):
                try:
                    expected_sha256 = task.sha256 or self._fetch_sha256(task)
                except (OSError, ValueError, KeyError, StopIteration):
                    return DownloadResult(
                        file=str(dest), status="metadata_failed", size=0, sha256="",
                        sha256_match=False, seconds=time.monotonic() - started,
                        segments=segments,
                    )
                if dest.exists():
                    actual_size = dest.stat().st_size
                    digest = self._sha256(dest)
                    if actual_size == task.size and digest == expected_sha256:
                        return DownloadResult(
                            file=str(dest), status="ok", size=actual_size, sha256=digest,
                            sha256_match=True, seconds=time.monotonic() - started,
                            segments=segments,
                        )
                return self._download_locked(task, segments, started, expected_sha256)
        except TimeoutError:
            return DownloadResult(
                file=str(dest), status="lock_timeout", size=0, sha256="",
                sha256_match=False, seconds=time.monotonic() - started,
                segments=segments,
            )

    def _download_locked(
        self, task: FileTask, segments: int, started: float, expected_sha256: str,
    ) -> DownloadResult:
        dest = Path(task.dest)
        parts = Path(f"{dest}.parts")
        if not self._prepare_layout(parts, task, segments):
            return DownloadResult(
                file=str(dest), status="segment_layout_mismatch", size=0,
                sha256="", sha256_match=False,
                seconds=time.monotonic() - started, segments=segments,
            )
        for index, (start, end) in enumerate(self._ranges(task.size, segments)):
            target = parts / f"p{index:03d}"
            try:
                self._download_segment(task, target, start, end)
            except OSError:
                return DownloadResult(
                    file=str(dest), status="download_failed", size=0, sha256="",
                    sha256_match=False, seconds=time.monotonic() - started,
                    segments=segments,
                )
        merged = Path(f"{dest}.merge")
        with merged.open("wb") as output:
            for index in range(segments):
                with (parts / f"p{index:03d}").open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
        actual_size = merged.stat().st_size
        digest = self._sha256(merged)
        match = actual_size == task.size and digest == expected_sha256
        if match:
            os.replace(merged, dest)
            shutil.rmtree(parts)
        else:
            merged.unlink()
        return DownloadResult(
            file=str(dest), status="ok" if match else "checksum_mismatch",
            size=actual_size, sha256=digest, sha256_match=match,
            seconds=time.monotonic() - started, segments=segments,
        )

    def _fetch_sha256(self, task: FileTask) -> str:
        repo = quote(task.repo, safe="/")
        path = quote(task.path, safe="/")
        url = f"{self.base_url}/api/models/{repo}/tree/main/{path}?expand=true"
        with urllib.request.urlopen(url, timeout=30) as response:
            metadata = json.load(response)
        if isinstance(metadata, list):
            metadata = next(
                item for item in metadata if item.get("path") == task.path
            )
        oid = metadata["lfs"]["oid"]
        digest = oid.removeprefix("sha256:")
        if len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest):
            raise ValueError("HF LFS metadata did not contain a SHA-256 oid")
        return digest.lower()

    def _request(self, task: FileTask, start: int, end: int):
        repo = quote(task.repo, safe="/")
        path = quote(task.path, safe="/")
        url = f"{self.base_url}/{repo}/resolve/main/{path}"
        return urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})

    @contextmanager
    def _destination_lock(self, dest: Path):
        path = Path(f"{dest}.lock")
        deadline = time.monotonic() + self.lock_timeout
        while True:
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for {path}")
                time.sleep(0.05)
        try:
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            yield
        finally:
            os.close(descriptor)
            path.unlink(missing_ok=True)

    @staticmethod
    def _prepare_layout(parts: Path, task: FileTask, segments: int) -> bool:
        manifest = parts / "layout.json"
        if parts.exists():
            existing = sorted(parts.glob("p[0-9][0-9][0-9]"))
            if manifest.exists():
                try:
                    layout = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return False
                return (
                    layout.get("segments") == segments
                    and layout.get("size") == task.size
                    and layout.get("repo") == task.repo
                    and layout.get("path") == task.path
                )
            if existing and len(existing) != segments:
                return False
        else:
            parts.mkdir()
        temporary = parts / "layout.json.tmp"
        temporary.write_text(
            json.dumps({
                "segments": segments, "size": task.size,
                "repo": task.repo, "path": task.path,
            }),
            encoding="utf-8",
        )
        os.replace(temporary, manifest)
        return True

    def _download_segment(self, task: FileTask, target: Path, start: int, end: int):
        expected = end - start + 1
        if expected == 0:
            target.touch()
            return
        for attempt in range(self.max_retries + 1):
            existing = target.stat().st_size if target.exists() else 0
            if existing == expected:
                return
            if existing > expected:
                raise OSError(f"segment {target} is larger than its layout")
            try:
                request = self._request(task, start + existing, end)
                connection_started = time.monotonic()
                received = 0
                with urllib.request.urlopen(
                    request, timeout=max(30, self.speed_time + 5),
                ) as response:
                    if response.status != 206:
                        raise OSError("server did not honor the requested byte range")
                    self._check_stall(connection_started, received)
                    with target.open("ab") as output:
                        while chunk := response.read(256 * 1024):
                            output.write(chunk)
                            received += len(chunk)
                            self._check_stall(connection_started, received)
                if target.stat().st_size == expected:
                    return
                raise OSError("segment ended before its requested range")
            except OSError:
                if attempt == self.max_retries:
                    raise
                time.sleep(self.backoff * (2**attempt))

    def _check_stall(self, started: float, received: int) -> None:
        elapsed = time.monotonic() - started
        if elapsed >= self.speed_time and received < elapsed * self.speed_limit:
            raise OSError(
                f"average speed below {self.speed_limit} B/s for {elapsed:.1f} s"
            )

    @staticmethod
    def _ranges(size: int, segments: int):
        base, extra = divmod(size, segments)
        offset = 0
        for index in range(segments):
            length = base + (index < extra)
            yield offset, offset + length - 1
            offset += length

    @staticmethod
    def _sha256(path: Path):
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()


def write_report(results: list[DownloadResult], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.tmp")
    temporary.write_text(
        json.dumps([asdict(result) for result in results], indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verified, resumable Hugging Face downloads")
    parser.add_argument("--plan", required=True, help="JSON download plan")
    parser.add_argument("--only", help="only files whose repo/path/dest contains this text")
    parser.add_argument("--dry-run", action="store_true", help="print selected files and exit")
    parser.add_argument("--report", default="report.json", help="JSON result report")
    parser.add_argument("--segments", type=int, default=6, help="segments per file (default: 6)")
    args = parser.parse_args(argv)

    with open(args.plan, encoding="utf-8") as source:
        tasks = Downloader().plan(json.load(source))
    if args.only:
        tasks = [
            task for task in tasks
            if args.only in f"{task.repo} {task.path} {task.dest}"
        ]
    if args.dry_run:
        print(json.dumps([asdict(task) for task in tasks], indent=2))
        return 0

    downloader = Downloader()
    results = [downloader.download(task, segments=args.segments) for task in tasks]
    write_report(results, args.report)
    print(json.dumps([asdict(result) for result in results], indent=2))
    return 0 if all(result.status == "ok" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
