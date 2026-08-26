"""Telegram notification helper for video pipeline delivery."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from video_pipeline.config import render_template

try:
    from unidecode import unidecode
except ImportError:
    def unidecode(text: str) -> str:
        """Fallback transliteration when unidecode is not installed."""
        return text.encode("ascii", "ignore").decode("ascii")


API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT = 30


class TelegramNotifierError(RuntimeError):
    """Raised when Telegram configuration or API calls fail."""


class TelegramNotifier:
    """Send videos and progress updates via Telegram Bot API."""

    def __init__(
        self,
        progress_every_min: int = 30,
        token: str | None = None,
        request_func: type = requests.request,
    ) -> None:
        """Initialize notifier.

        Args:
            progress_every_min: Minimum minutes between progress messages.
            token: Bot token; reads TELEGRAM_BOT_TOKEN env if omitted.
            request_func: Injectable request function for testing.
        """
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise TelegramNotifierError(
                "TELEGRAM_BOT_TOKEN environment variable is required for Telegram delivery"
            )
        self.progress_every_min = progress_every_min
        self._last_progress_at: datetime | None = None
        self._request_func = request_func

    def render(self, template: str, **context: object) -> str:
        """Render a template with context values.

        Args:
            template: Template string with ``{key}`` placeholders.
            **context: Substitution values.

        Returns:
            Rendered string.
        """
        return render_template(template, **context)

    def _sanitize_caption(self, text: str) -> str:
        """Transliterate caption text for cp1251-safe Telegram captions."""
        return unidecode(text)

    def should_send_progress(self) -> bool:
        """Return True if enough time has passed since the last progress message."""
        if self._last_progress_at is None:
            return True
        elapsed = datetime.now(timezone.utc) - self._last_progress_at
        return elapsed.total_seconds() >= self.progress_every_min * 60

    def send_text(self, text: str, chat_id: int) -> int:
        """Send a text message.

        Args:
            text: Message body.
            chat_id: Telegram chat ID.

        Returns:
            Telegram message_id.
        """
        url = f"{API_BASE}/bot{self.token}/sendMessage"
        response = self._request_func(
            "POST",
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise TelegramNotifierError(payload.get("description", "sendMessage failed"))
        return int(payload["result"]["message_id"])

    def send_video(self, path: Path, caption: str, chat_id: int) -> int:
        """Send a video file with caption.

        Args:
            path: Path to the video file.
            caption: Video caption (transliterated for safety).
            chat_id: Telegram chat ID.

        Returns:
            Telegram message_id.
        """
        if not path.exists():
            raise FileNotFoundError(f"Video not found: {path}")

        safe_caption = self._sanitize_caption(caption)
        url = f"{API_BASE}/bot{self.token}/sendVideo"
        with path.open("rb") as video_file:
            response = self._request_func(
                "POST",
                url,
                data={"chat_id": str(chat_id), "caption": safe_caption},
                files={"video": (path.name, video_file, "video/mp4")},
                timeout=DEFAULT_TIMEOUT,
            )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise TelegramNotifierError(payload.get("description", "sendVideo failed"))
        return int(payload["result"]["message_id"])

    def send_progress(self, text: str, chat_id: int) -> int | None:
        """Send a throttled progress message.

        Args:
            text: Progress message text.
            chat_id: Telegram chat ID.

        Returns:
            message_id if sent, None if throttled.
        """
        if not self.should_send_progress():
            return None
        message_id = self.send_text(text, chat_id)
        self._last_progress_at = datetime.now(timezone.utc)
        return message_id
