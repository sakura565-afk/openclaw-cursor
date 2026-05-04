#!/usr/bin/env python3
"""Telegram bot sender utility for OpenClaw."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, IO, Iterable, Optional, Sequence

import requests

from scripts.media_tool import DEFAULT_PHOTO_LIMIT_BYTES, PreparedFile, ensure_photo_size_under_limit


API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_RETRIES = 3
DEFAULT_GROUP_LIMIT = 10
DOCUMENT_SIZE_LIMIT_BYTES = 50 * 1024 * 1024


class TelegramSenderError(RuntimeError):
    """Raised when a Telegram upload fails permanently."""


def _default_reporter(message: str) -> None:
    print(message, file=sys.stderr)


class ProgressFile:
    """File wrapper that reports read progress during uploads."""

    def __init__(
        self,
        path: Path,
        mode: str = "rb",
        reporter: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._path = Path(path)
        self._reporter = reporter or _default_reporter
        self._handle = self._path.open(mode)
        self._size = self._path.stat().st_size
        self._bytes_read = 0
        self._last_reported_percent = -1

    @property
    def name(self) -> str:
        return self._path.name

    def read(self, size: int = -1) -> bytes:
        chunk = self._handle.read(size)
        if chunk:
            self._bytes_read += len(chunk)
            self._emit_progress()
        elif self._size == 0 and self._last_reported_percent < 100:
            self._last_reported_percent = 100
            self._reporter(f"Upload progress for {self._path.name}: 100% (0 bytes)")
        return chunk

    def close(self) -> None:
        self._handle.close()

    def __getattr__(self, item: str) -> object:
        return getattr(self._handle, item)

    def __enter__(self) -> "ProgressFile":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _emit_progress(self) -> None:
        if self._size <= 0:
            return
        percent = int((self._bytes_read / self._size) * 100)
        if percent > 100:
            percent = 100
        should_report = percent == 100 or percent // 10 > self._last_reported_percent // 10
        if should_report:
            self._last_reported_percent = percent
            self._reporter(
                f"Upload progress for {self._path.name}: {percent}% "
                f"({self._bytes_read}/{self._size} bytes)"
            )


@dataclass
class TelegramConfig:
    token: str
    chat_id: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    retries: int = DEFAULT_RETRIES

    @classmethod
    def from_env(
        cls,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
    ) -> "TelegramConfig":
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        missing = [
            name
            for name, value in (
                ("TELEGRAM_BOT_TOKEN", token),
                ("TELEGRAM_CHAT_ID", chat_id),
            )
            if not value
        ]
        if missing:
            raise TelegramSenderError(
                "Missing required environment variables: " + ", ".join(missing)
            )
        return cls(
            token=token,
            chat_id=chat_id,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )


def _build_url(config: TelegramConfig, method: str) -> str:
    return f"{API_BASE}/bot{config.token}/{method}"


def _guess_mime_type(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    return mime_type or "application/octet-stream"


class TelegramSender:
    """Client wrapper around the Telegram Bot API."""

    def __init__(
        self,
        config: TelegramConfig,
        *,
        reporter: Optional[Callable[[str], None]] = None,
        request_func: Callable[..., requests.Response] = requests.request,
        sleep_func: Callable[[float], None] = time.sleep,
        resize_func: Callable[..., PreparedFile] = ensure_photo_size_under_limit,
    ) -> None:
        self.config = config
        self.reporter = reporter or _default_reporter
        self.request_func = request_func
        self.sleep_func = sleep_func
        self.resize_func = resize_func

    def send_photo(self, image_path: os.PathLike[str] | str, caption: str = "") -> dict:
        prepared = self.resize_func(image_path, reporter=self.reporter)
        try:
            self.reporter(
                f"Sending photo {prepared.path.name} "
                f"({prepared.final_size or prepared.path.stat().st_size} bytes)."
            )
            with ProgressFile(prepared.path, reporter=self.reporter) as photo_handle:
                response = self._post(
                    "sendPhoto",
                    data={"chat_id": self.config.chat_id, "caption": caption},
                    files={
                        "photo": (
                            prepared.path.name,
                            photo_handle,
                            _guess_mime_type(prepared.path),
                        )
                    },
                )
            return response.json()
        finally:
            prepared.cleanup()

    def send_document(self, file_path: os.PathLike[str] | str, caption: str = "") -> dict:
        document = Path(file_path)
        if not document.exists():
            raise FileNotFoundError(f"Document not found: {document}")
        size = document.stat().st_size
        if size > DOCUMENT_SIZE_LIMIT_BYTES:
            raise TelegramSenderError(
                f"Document exceeds size limit of {DOCUMENT_SIZE_LIMIT_BYTES} bytes: {document}"
            )
        self.reporter(f"Sending document {document.name} ({size} bytes).")
        with ProgressFile(document, reporter=self.reporter) as document_handle:
            response = self._post(
                "sendDocument",
                data={"chat_id": self.config.chat_id, "caption": caption},
                files={"document": (document.name, document_handle, "application/octet-stream")},
            )
        return response.json()

    def send_group(self, image_paths: Sequence[os.PathLike[str] | str], caption: str = "") -> dict:
        if not image_paths:
            raise TelegramSenderError("Media group requires at least one image.")
        if len(image_paths) > DEFAULT_GROUP_LIMIT:
            raise TelegramSenderError(
                f"Media groups support at most {DEFAULT_GROUP_LIMIT} images."
            )

        prepared_items: list[PreparedFile] = []
        handles: list[IO[bytes]] = []
        files: dict[str, tuple[str, IO[bytes], str]] = {}
        media_entries: list[dict[str, str]] = []
        try:
            for index, path in enumerate(image_paths):
                prepared = self.resize_func(path, reporter=self.reporter)
                prepared_items.append(prepared)
                field_name = f"photo{index}"
                handle = ProgressFile(prepared.path, reporter=self.reporter)
                handles.append(handle)
                files[field_name] = (
                    prepared.path.name,
                    handle,
                    _guess_mime_type(prepared.path),
                )
                entry = {"type": "photo", "media": f"attach://{field_name}"}
                if index == 0 and caption:
                    entry["caption"] = caption
                media_entries.append(entry)

            self.reporter(f"Sending media group with {len(media_entries)} image(s).")
            response = self._post(
                "sendMediaGroup",
                data={"chat_id": self.config.chat_id, "media": json.dumps(media_entries)},
                files=files,
            )
            return response.json()
        finally:
            for handle in handles:
                handle.close()
            for prepared in prepared_items:
                prepared.cleanup()

    def _post(self, method_name: str, **kwargs) -> requests.Response:
        url = _build_url(self.config, method_name)
        return self._request_with_retries(
            method="POST",
            url=url,
            reporter=self.reporter,
            retries=self.config.retries,
            timeout=self.config.timeout_seconds,
            **kwargs,
        )

    def _request_with_retries(self, **kwargs) -> requests.Response:
        last_error: Optional[BaseException] = None
        for attempt in range(1, self.config.retries + 1):
            self.reporter(f"Request attempt {attempt}/{self.config.retries}.")
            try:
                response = self.request_func(**kwargs)
                response.raise_for_status()
                payload = response.json()
                if not payload.get("ok", False):
                    raise TelegramSenderError(
                        payload.get("description", "Telegram API returned ok=false")
                    )
                self.reporter(f"Request succeeded on attempt {attempt}.")
                return response
            except (requests.RequestException, ValueError, TelegramSenderError) as exc:
                last_error = exc
                if attempt >= self.config.retries:
                    break
                wait_seconds = 2 ** (attempt - 1)
                self.reporter(
                    f"Attempt {attempt} failed: {exc}. Retrying in {wait_seconds} seconds."
                )
                self.sleep_func(wait_seconds)
        raise TelegramSenderError(
            f"Telegram request failed after {self.config.retries} attempts: {last_error}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send media to Telegram via bot API.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="HTTP request timeout in seconds.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help="Maximum upload attempts before failing.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    send_photo = subparsers.add_parser("send-photo", help="Send a single photo.")
    send_photo.add_argument("image", help="Path to an image file.")
    send_photo.add_argument("--caption", default="", help="Optional photo caption.")

    send_group = subparsers.add_parser("send-group", help="Send a media group.")
    send_group.add_argument("images", nargs="+", help="One or more image paths.")
    send_group.add_argument("--caption", default="", help="Optional caption on the first image.")

    send_document = subparsers.add_parser("send-document", help="Send a document upload.")
    send_document.add_argument("file", help="Path to the file to upload.")
    send_document.add_argument("--caption", default="", help="Optional document caption.")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = TelegramConfig.from_env(timeout_seconds=args.timeout, retries=args.retries)
    sender = TelegramSender(config)

    if args.command == "send-photo":
        payload = sender.send_photo(args.image, caption=args.caption)
    elif args.command == "send-group":
        payload = sender.send_group(args.images, caption=args.caption)
    elif args.command == "send-document":
        payload = sender.send_document(args.file, caption=args.caption)
    else:
        parser.error(f"Unsupported command: {args.command}")
        return 2

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
