"""Verified, resumable HTTP downloads (stdlib only)."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

ProgressCallback = Callable[[int, Optional[int]], None]


class DownloadVerificationError(Exception):
    """Raised when size or checksum verification fails after all retries."""


def build_headers(kind: str = "url", token: str | None = None) -> dict[str, str]:
    """Build request headers for plain URL, HuggingFace, or CivitAI downloads."""
    headers: dict[str, str] = {}
    if kind == "hf":
        tok = token or os.environ.get("HF_TOKEN")
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
    elif kind == "civitai":
        tok = token or os.environ.get("CIVITAI_API_TOKEN")
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
    return headers


def build_url(url: str, kind: str = "url", token: str | None = None) -> str:
    """Augment download URL (CivitAI token query param when applicable)."""
    if kind != "civitai":
        return url
    tok = token or os.environ.get("CIVITAI_API_TOKEN")
    if not tok:
        return url
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    if "token" not in qs:
        qs["token"] = [tok]
    new_query = urllib.parse.urlencode(qs, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


def head_size(url: str, headers: dict[str, str] | None = None, timeout: int = 60) -> int | None:
    """Return Content-Length via HEAD, or total size from a ranged GET fallback."""
    hdrs = dict(headers or {})

    def _content_length_from_head() -> int | None:
        req = urllib.request.Request(url, method="HEAD", headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                cl = resp.headers.get("Content-Length")
                if cl is not None:
                    return int(cl)
        except urllib.error.HTTPError as exc:
            if exc.code not in (405, 501):
                raise
        except urllib.error.URLError:
            pass
        return None

    size = _content_length_from_head()
    if size is not None:
        return size

    range_hdrs = {**hdrs, "Range": "bytes=0-0"}
    req = urllib.request.Request(url, headers=range_hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        cr = resp.headers.get("Content-Range")
        if not cr:
            cl = resp.headers.get("Content-Length")
            return int(cl) if cl is not None else None
        total = cr.split("/")[-1].strip()
        if total == "*":
            return None
        return int(total)


def _stream_sha256(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _write_response_body(
    resp: object,
    part_path: str,
    mode: str,
    chunk: int,
    progress: ProgressCallback | None,
    bytes_already: int,
    total_bytes: int | None,
) -> int:
    """Stream response body to part_path; return total bytes on disk after write."""
    written = bytes_already if mode == "ab" else 0
    with open(part_path, mode) as out:
        while True:
            block = resp.read(chunk)
            if not block:
                break
            out.write(block)
            written += len(block)
            if progress is not None:
                progress(written, total_bytes)
    return written


def download(
    url: str,
    dest: str,
    expected_size: int | None = None,
    sha256: str | None = None,
    resume: bool = True,
    chunk: int = 1 << 20,
    max_retries: int = 5,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
    progress: ProgressCallback | None = None,
) -> dict:
    """
    Download url into dest with optional size and SHA256 verification.

    Uses dest + ".part" during transfer; only replaces dest after verification.
    """
    hdrs = dict(headers or {})
    part_path = dest + ".part"
    dest_dir = os.path.dirname(dest)
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)

    discovered_size = head_size(url, hdrs, timeout=timeout)
    target_size = expected_size if expected_size is not None else discovered_size

    resumed_any = False
    attempts = 0
    last_error: str | None = None

    for attempt in range(1, max_retries + 1):
        attempts = attempt
        start_byte = 0
        if resume and os.path.isfile(part_path):
            start_byte = os.path.getsize(part_path)
            if start_byte > 0:
                resumed_any = True

        req_headers = dict(hdrs)
        if start_byte > 0:
            req_headers["Range"] = f"bytes={start_byte}-"

        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", resp.getcode())

                if start_byte > 0 and status == 200:
                    start_byte = 0
                    mode = "wb"
                elif start_byte > 0 and status == 206:
                    mode = "ab"
                else:
                    mode = "wb"

                bytes_before = start_byte if mode == "ab" else 0
                _write_response_body(
                    resp,
                    part_path,
                    mode,
                    chunk,
                    progress,
                    bytes_before,
                    target_size,
                )
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = str(exc)
            if os.path.isfile(part_path):
                try:
                    os.remove(part_path)
                except OSError:
                    pass
            continue

        if not os.path.isfile(part_path):
            last_error = "download produced no data"
            continue

        actual_size = os.path.getsize(part_path)
        if target_size is not None and actual_size != target_size:
            last_error = f"size mismatch: got {actual_size}, expected {target_size}"
            try:
                os.remove(part_path)
            except OSError:
                pass
            continue

        file_hash = _stream_sha256(part_path, chunk=chunk)
        if sha256 is not None and file_hash.lower() != sha256.lower():
            last_error = f"sha256 mismatch: got {file_hash}, expected {sha256}"
            try:
                os.remove(part_path)
            except OSError:
                pass
            continue

        os.replace(part_path, dest)
        return {
            "url": url,
            "dest": dest,
            "bytes": actual_size,
            "sha256": file_hash,
            "resumed": resumed_any,
            "attempts": attempts,
        }

    msg = last_error or "download failed"
    raise DownloadVerificationError(
        f"verification failed for {url} after {attempts} attempt(s): {msg}"
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verified HTTP download")
    parser.add_argument("url", help="Download URL")
    parser.add_argument("--dest", required=True, help="Destination file path")
    parser.add_argument("--sha256", help="Expected SHA256 hex digest")
    parser.add_argument("--expect-size", type=int, dest="expect_size", help="Expected byte size")
    parser.add_argument("--no-resume", action="store_true", help="Do not resume partial downloads")
    parser.add_argument(
        "--kind",
        choices=("url", "hf", "civitai"),
        default="url",
        help="URL kind for auth headers",
    )
    parser.add_argument("--token", help="API token (overrides env for hf/civitai)")
    args = parser.parse_args(argv)

    url = build_url(args.url, kind=args.kind, token=args.token)
    hdrs = build_headers(kind=args.kind, token=args.token)

    def _progress(done: int, total: int | None) -> None:
        if total:
            pct = 100.0 * done / total
            print(f"\r{done}/{total} bytes ({pct:.1f}%)", end="", flush=True)
        else:
            print(f"\r{done} bytes", end="", flush=True)

    try:
        result = download(
            url,
            args.dest,
            expected_size=args.expect_size,
            sha256=args.sha256,
            resume=not args.no_resume,
            headers=hdrs,
            progress=_progress,
        )
    except DownloadVerificationError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 1

    print(
        f"\nOK: {result['dest']} ({result['bytes']} bytes, sha256={result['sha256']}, "
        f"resumed={result['resumed']}, attempts={result['attempts']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
