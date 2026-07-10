#!/usr/bin/env python3
"""Diff + Telegram alert helper for the product catalog pipeline.

Builds a Markdown alert message from a :class:`DiffReport` when the diff
exceeds configurable thresholds, and provides a CLI that parses a site,
computes the diff and (optionally) sends the alert to Telegram.

Telegram credentials are read from the environment:
``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import requests

# Allow running as a plain script (``python scripts/product_catalog_diff.py``)
# as well as a module (``python -m scripts.product_catalog_diff``).
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.product_catalog import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_DB_PATH,
    DiffReport,
    ProductCatalogParser,
)

logger = logging.getLogger("product_catalog_diff")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _price_change_pct(changes: dict) -> float | None:
    """Percentage price change for a single changed product, if applicable."""
    price = changes.get("changes", {}).get("price_rub")
    if not price:
        return None
    old = price.get("old")
    new = price.get("new")
    if old in (None, 0) or new is None:
        return None
    try:
        return abs(new - old) / abs(old) * 100.0
    except (TypeError, ZeroDivisionError):
        return None


def diff_alert_message(
    site: str,
    diff: DiffReport,
    threshold_added: int = 5,
    threshold_price_change_pct: float = 10.0,
) -> str | None:
    """Return a Markdown Telegram message if the diff exceeds thresholds.

    Triggers when either the number of newly added products meets/exceeds
    ``threshold_added`` OR any product's price changed by at least
    ``threshold_price_change_pct`` percent. Returns ``None`` otherwise.
    """
    big_price_moves = []
    for item in diff.changed:
        pct = _price_change_pct(item)
        if pct is not None and pct >= threshold_price_change_pct:
            big_price_moves.append((item["sku"], item["changes"]["price_rub"], pct))

    added_triggered = len(diff.added) >= threshold_added
    price_triggered = bool(big_price_moves)
    if not (added_triggered or price_triggered):
        return None

    lines = [
        f"*Catalog changes: {site}*",
        f"_run {diff.prev_run_id} -> {diff.curr_run_id}_",
        "",
        f"Added: *{len(diff.added)}*  |  Removed: *{len(diff.removed)}*  |  "
        f"Changed: *{len(diff.changed)}*",
    ]

    if added_triggered and diff.added:
        lines.append("")
        lines.append("*New products:*")
        for item in diff.added[:10]:
            price = item.get("price_rub")
            price_str = f"{price:.0f} ₽" if isinstance(price, (int, float)) else "n/a"
            lines.append(f"• {item.get('title') or item['sku']} — {price_str}")
        if len(diff.added) > 10:
            lines.append(f"…and {len(diff.added) - 10} more")

    if price_triggered:
        lines.append("")
        lines.append(f"*Price moves ≥ {threshold_price_change_pct:.0f}%:*")
        for sku, price, pct in big_price_moves[:10]:
            direction = "↑" if price["new"] > price["old"] else "↓"
            lines.append(
                f"• {sku}: {price['old']:.0f} → {price['new']:.0f} ₽ "
                f"({direction}{pct:.0f}%)"
            )

    return "\n".join(lines)


def send_telegram(message: str, *, token: str, chat_id: str,
                  session: requests.Session | None = None) -> bool:
    """Send a Markdown message to Telegram. Returns True on success."""
    session = session or requests.Session()
    url = TELEGRAM_API.format(token=token)
    try:
        response = session.post(
            url,
            data={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("telegram_send_failed: %s", exc)
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Catalog diff + Telegram alert.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Parse + diff + alert.")
    p_run.add_argument("--site", required=True)
    p_run.add_argument("--dry-run", action="store_true",
                       help="Discover only; do not persist or alert.")
    p_run.add_argument("--max-pages", type=int, default=5000)
    p_run.add_argument("--delay", type=float, default=1.0)
    p_run.add_argument("--threshold-added", type=int, default=5)
    p_run.add_argument("--threshold-price-pct", type=float, default=10.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "run":
        parser.error(f"Unknown command: {args.command}")
        return 2

    catalog = ProductCatalogParser(
        args.site, Path(args.db), dry_run=args.dry_run,
        max_pages=args.max_pages, delay_s=args.delay, config_path=args.config,
    )
    summary = catalog.run()
    print(
        f"[{summary.site}] discovered={summary.urls_discovered} "
        f"fetched_ok={summary.urls_fetched_ok} added={summary.added} "
        f"changed={summary.changed} removed={summary.removed}",
        file=sys.stderr,
    )
    if args.dry_run:
        return 0

    diff = catalog.diff_since_last()
    message = diff_alert_message(
        args.site, diff,
        threshold_added=args.threshold_added,
        threshold_price_change_pct=args.threshold_price_pct,
    )
    if message is None:
        print("No alert: diff below threshold.", file=sys.stderr)
        return 0

    print(message)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; skipping Telegram send."
        )
        return 0
    ok = send_telegram(message, token=token, chat_id=chat_id)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
