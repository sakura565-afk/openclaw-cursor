#!/usr/bin/env python3
"""
Daily Brief: Gmail + Google Calendar → Telegram digest.

Uses Composio SDK for Gmail/Calendar access, Telegram Bot API (requests)
for delivery. Reads secrets from environment variables.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("daily_brief")

# ---------------------------------------------------------------------------
# Config from env
# ---------------------------------------------------------------------------
COMPOSIO_API_KEY: str | None = os.getenv("COMPOSIO_API_KEY")
TELEGRAM_BOT_TOKEN: str | None = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID: str | None = os.getenv("TELEGRAM_CHAT_ID")

# Moscow timezone (UTC+3) — used for calendar day queries
TZ_MOSCOW = timezone.utc  # we'll apply +03:00 offset manually


def check_env() -> None:
    """Verify all required env vars are present."""
    missing = []
    for var in ("COMPOSIO_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if not os.getenv(var):
            missing.append(var)
    if missing:
        log.error("Missing required env vars: %s", missing)
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Telegram delivery
# ---------------------------------------------------------------------------
def send_telegram(message: str) -> None:
    """Send a text message via Telegram bot."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    log.info("Telegram message sent, chat_id=%s", TELEGRAM_CHAT_ID)


# ---------------------------------------------------------------------------
# Composio – Gmail
# ---------------------------------------------------------------------------
def fetch_gmail_messages(client: Any, max_results: int = 5) -> list[dict[str, str]]:
    """Fetch recent unread emails via Composio Gmail tool."""
    try:
        result = client.execute(
            tool_name="gmail_fetch_emails",
            arguments={
                "query": "is:unread",
                "user_id": "me",
                "max_results": max_results,
                "verbose": False,
                "include_payload": False,
            },
        )
    except Exception as exc:
        log.warning("Gmail fetch failed: %s", exc)
        return []

    messages: list[dict[str, str]] = []
    data = result.get("data", {}) if isinstance(result, dict) else {}
    items = data.get("messages", []) if isinstance(data, dict) else []

    for item in items[:max_results]:
        if isinstance(item, dict):
            messages.append({
                "from": item.get("from", "—"),
                "subject": item.get("subject", "—"),
                "snippet": item.get("snippet", "")[:80],
            })
        elif isinstance(item, str):
            messages.append({"from": "—", "subject": item, "snippet": ""})

    return messages


# ---------------------------------------------------------------------------
# Composio – Google Calendar
# ---------------------------------------------------------------------------
def fetch_today_events(client: Any) -> list[dict[str, Any]]:
    """Fetch today's calendar events via Composio Google Calendar tool.

    Uses Moscow (UTC+3) local date boundaries. The Composio Google Calendar
    tool requires RFC3339 timestamps with mandatory timezone offset in
    timeMin/timeMax — bare UTC 'Z' dates would miss events on the local date.
    """
    # Moscow = UTC+3 — use local date with explicit offset
    today = datetime.now().strftime("%Y-%m-%d")
    time_min = f"{today}T00:00:00+03:00"
    time_max = f"{today}T23:59:59+03:00"

    try:
        result = client.execute(
            tool_name="googlecalendar_events_list",
            arguments={
                "calendarId": "primary",
                "timeMin": time_min,
                "timeMax": time_max,
                "maxResults": 20,
                "singleEvents": True,
                "orderBy": "startTime",
            },
        )
    except Exception as exc:
        log.warning("Calendar fetch failed: %s", exc)
        return []

    events: list[dict[str, Any]] = []
    data = result.get("data", {}) if isinstance(result, dict) else {}
    items = data if isinstance(data, list) else data.get("items", [])

    for item in items:
        if not isinstance(item, dict):
            continue
        start = item.get("start", {})
        end = item.get("end", {})
        start_str = start.get("dateTime", start.get("date", ""))
        summary = item.get("summary", "Без названия")

        # Extract HH:MM from ISO datetime
        time_display = ""
        if "T" in start_str:
            try:
                # Remove UTC offset before extracting time
                naive = start_str.split("+")[0] if "+" in start_str else start_str
                time_display = naive.split("T")[1][:5]
            except (IndexError, ValueError):
                time_display = start_str

        events.append({"time": time_display, "summary": summary})

    return events


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def format_digest(events: list[dict[str, Any]], emails: list[dict[str, str]]) -> str:
    """Build the Russian-language digest string."""
    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    lines: list[str] = []

    lines.append("📅 *Календарь на сегодня:*\n")
    if events:
        sorted_events = sorted(events, key=lambda e: e.get("time", ""))
        for ev in sorted_events:
            time_str = ev.get("time", "??:??").replace(":", ":")
            title = ev.get("summary", "—")
            lines.append(f"  🕐 {time_str} — {title}")
    else:
        lines.append("  Нет событий")

    lines.append("")
    lines.append("📧 *Непрочитанные письма:*\n")
    if emails:
        for em in emails:
            sender = em.get("from", "—")
            subject = em.get("subject", "—")
            snippet = em.get("snippet", "")
            lines.append(f"  ✉️ От: {sender}")
            lines.append(f"     Тема: {subject}")
            if snippet:
                lines.append(f"     ({snippet}…)")
            lines.append("")
    else:
        lines.append("  Нет непрочитанных писем")

    lines.append("")
    lines.append(f"⏰ Время формирования: {now}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    check_env()

    log.info("Daily Brief started")

    from composio import Composio
    client = Composio(api_key=COMPOSIO_API_KEY)

    log.info("Fetching Gmail messages…")
    emails = fetch_gmail_messages(client, max_results=5)
    log.info("Got %d emails", len(emails))

    log.info("Fetching today's calendar events…")
    events = fetch_today_events(client)
    log.info("Got %d events", len(events))

    digest = format_digest(events, emails)
    log.info("Sending digest to Telegram…")
    send_telegram(digest)
    log.info("Daily Brief complete")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())