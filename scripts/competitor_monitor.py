#!/usr/bin/env python3
"""Competitor monitor: Avito + Wildberries furniture listings with SQLite history.

Extends ``scrap_furniture`` with persistence, price history, change detection,
and weekly Markdown digests under ``scripts/data/competitors/``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from sqlite_helper import connect_sqlite  # noqa: E402
from scrap_furniture import FurnitureListing, FurnitureScrapeSession, scrape_avito, scrape_wb  # noqa: E402
DEFAULT_DB = SCRIPT_DIR / "data" / "competitors.db"
DIGEST_DIR = SCRIPT_DIR / "data" / "competitors"

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _parse_iso_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def init_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            query TEXT NOT NULL,
            title TEXT NOT NULL,
            price REAL,
            url TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            UNIQUE(source, url)
        );

        CREATE INDEX IF NOT EXISTS idx_listings_query ON listings(source, query);
        CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);

        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL,
            price REAL,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (listing_id) REFERENCES listings(id)
        );

        CREATE INDEX IF NOT EXISTS idx_price_history_listing ON price_history(listing_id, id);
        """
    )
    conn.commit()


def _record_price(conn, listing_id: int, price: float | None, when: str) -> None:
    if price is None:
        return
    conn.execute(
        "INSERT INTO price_history (listing_id, price, recorded_at) VALUES (?, ?, ?)",
        (listing_id, price, when),
    )


def _fetch_listing_row(conn, source: str, url: str) -> dict[str, Any] | None:
    cur = conn.execute(
        "SELECT * FROM listings WHERE source = ? AND url = ?",
        (source, url),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def upsert_listing(
    conn,
    item: FurnitureListing,
    now: str,
) -> tuple[str, dict[str, Any]]:
    """
    Insert or update a listing row; maintain price_history on changes.

    Returns (event, detail) where event is one of:
    new, price_change, discount, reactivated, updated, unchanged
    """
    row = _fetch_listing_row(conn, item.source, item.url)
    if row is None:
        status = "sold" if item.sold else "active"
        conn.execute(
            """
            INSERT INTO listings (source, query, title, price, url, first_seen, last_seen, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (item.source, item.query, item.title, item.price, item.url, now, now, status),
        )
        lid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        _record_price(conn, lid, item.price, now)
        conn.commit()
        return ("new", {"url": item.url, "title": item.title, "price": item.price})

    lid = int(row["id"])
    old_price = row["price"]
    old_status = row["status"]

    new_status = "sold" if item.sold else "active"
    if old_status in ("gone", "sold") and new_status == "active":
        event = "reactivated"
    elif item.price is not None and old_price is not None and item.price != old_price:
        if item.price < old_price:
            event = "discount"
        else:
            event = "price_change"
    elif item.title != row["title"] or item.query != row["query"]:
        event = "updated"
    else:
        event = "unchanged"

    conn.execute(
        """
        UPDATE listings
        SET query = ?, title = ?, price = ?, last_seen = ?, status = ?
        WHERE id = ?
        """,
        (item.query, item.title, item.price, now, new_status, lid),
    )

    if item.price is not None and old_price is not None and item.price != old_price:
        _record_price(conn, lid, item.price, now)
    elif item.price is not None and old_price is None:
        _record_price(conn, lid, item.price, now)

    conn.commit()
    detail = {
        "url": item.url,
        "title": item.title,
        "old_price": old_price,
        "new_price": item.price,
    }
    return (event, detail)


def mark_missing_inactive(
    conn,
    source: str,
    query: str,
    seen_urls: set[str],
    now: str,
) -> list[str]:
    """Mark listings no longer in SERP as ``gone`` (or ``sold`` if already sold)."""
    cur = conn.execute(
        """
        SELECT id, url, status FROM listings
        WHERE source = ? AND query = ? AND status IN ('active', 'sold')
        """,
        (source, query),
    )
    gone_urls: list[str] = []
    for r in cur.fetchall():
        url = str(r["url"])
        if url in seen_urls:
            continue
        gone_urls.append(url)
        new_status = "sold" if r["status"] == "sold" else "gone"
        conn.execute(
            "UPDATE listings SET status = ?, last_seen = ? WHERE id = ?",
            (new_status, now, int(r["id"])),
        )
    conn.commit()
    return gone_urls


def sync_queries(
    conn,
    session: FurnitureScrapeSession,
    avito: Sequence[tuple[str, str]],
    wb: Sequence[tuple[str, int]],
    *,
    skip_empty_scrape: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """
    Run scrapes and update DB.

    ``avito`` / ``wb`` entries are ``(query, region_or_pages)`` — region path for Avito,
    max pages (int) for WB.
    """
    now = _iso_now()
    summary: dict[str, list] = defaultdict(list)

    for query, region in avito:
        try:
            listings = scrape_avito(session, query, region_path=region)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Avito scrape failed for %r: %s", query, exc)
            summary["errors"].append({"source": "avito", "query": query, "error": str(exc)})
            continue
        if skip_empty_scrape and not listings:
            logger.warning("Avito returned 0 rows for %r — not marking missing listings.", query)
            summary["skipped"].append({"source": "avito", "query": query})
            continue
        seen = {x.url for x in listings}
        for item in listings:
            ev, det = upsert_listing(conn, item, now)
            if ev != "unchanged":
                summary[ev].append({"source": "avito", **det})
        gone = mark_missing_inactive(conn, "avito", query, seen, now)
        for u in gone:
            summary["disappeared"].append({"source": "avito", "query": query, "url": u})

    for query, pages in wb:
        try:
            listings = scrape_wb(session, query, max_pages=int(pages))
        except Exception as exc:  # noqa: BLE001
            logger.exception("WB scrape failed for %r: %s", query, exc)
            summary["errors"].append({"source": "wb", "query": query, "error": str(exc)})
            continue
        if skip_empty_scrape and not listings:
            logger.warning("WB returned 0 rows for %r — not marking missing listings.", query)
            summary["skipped"].append({"source": "wb", "query": query})
            continue
        seen = {x.url for x in listings}
        for item in listings:
            ev, det = upsert_listing(conn, item, now)
            if ev != "unchanged":
                summary[ev].append({"source": "wb", **det})
        gone = mark_missing_inactive(conn, "wb", query, seen, now)
        for u in gone:
            summary["disappeared"].append({"source": "wb", "query": query, "url": u})

    return dict(summary)


def _week_window(week_end: date) -> tuple[str, str]:
    start = week_end - timedelta(days=6)
    start_s = start.isoformat() + "T00:00:00Z"
    end_s = (week_end + timedelta(days=1)).isoformat() + "T00:00:00Z"
    return start_s, end_s


def collect_digest_rows(conn, week_end: date) -> dict[str, Any]:
    start_s, end_s = _week_window(week_end)

    new_rows = conn.execute(
        """
        SELECT source, query, title, price, url, first_seen, status
        FROM listings
        WHERE first_seen >= ? AND first_seen < ?
        ORDER BY source, query, first_seen
        """,
        (start_s, end_s),
    ).fetchall()

    gone_rows = conn.execute(
        """
        SELECT source, query, title, price, url, last_seen, status
        FROM listings
        WHERE status IN ('gone', 'sold')
          AND last_seen >= ? AND last_seen < ?
        ORDER BY source, query, last_seen
        """,
        (start_s, end_s),
    ).fetchall()

    price_events = conn.execute(
        """
        WITH ordered AS (
            SELECT
                ph.id,
                ph.listing_id,
                ph.price AS new_price,
                ph.recorded_at,
                LAG(ph.price) OVER (PARTITION BY ph.listing_id ORDER BY ph.id) AS old_price
            FROM price_history ph
        )
        SELECT l.source, l.query, l.title, l.url, o.old_price, o.new_price, o.recorded_at
        FROM ordered o
        JOIN listings l ON l.id = o.listing_id
        WHERE o.old_price IS NOT NULL
          AND o.old_price != o.new_price
          AND o.recorded_at >= ? AND o.recorded_at < ?
        ORDER BY o.recorded_at
        """,
        (start_s, end_s),
    ).fetchall()

    discounts = [r for r in price_events if r["new_price"] is not None and r["old_price"] is not None]
    discounts = [r for r in discounts if float(r["new_price"]) < float(r["old_price"])]
    increases = [r for r in price_events if r["new_price"] is not None and r["old_price"] is not None]
    increases = [r for r in increases if float(r["new_price"]) > float(r["old_price"])]

    return {
        "week_start": (week_end - timedelta(days=6)).isoformat(),
        "week_end": week_end.isoformat(),
        "new_listings": [dict(r) for r in new_rows],
        "removed": [dict(r) for r in gone_rows],
        "price_changes": [dict(r) for r in price_events],
        "discounts": [dict(r) for r in discounts],
        "price_increases": [dict(r) for r in increases],
    }


def render_weekly_digest_md(data: dict[str, Any]) -> str:
    ws, we = data["week_start"], data["week_end"]
    lines = [
        f"# Competitor weekly digest",
        "",
        f"- **Window:** {ws} — {we} (UTC date boundaries)",
        "",
        "## Summary",
        "",
        f"- New listings observed: **{len(data['new_listings'])}**",
        f"- Removed / sold (status change this week): **{len(data['removed'])}**",
        f"- Any price change: **{len(data['price_changes'])}**",
        f"-  - Discounts (price down): **{len(data['discounts'])}**",
        f"-  - Price up: **{len(data['price_increases'])}**",
        "",
        "## New listings",
        "",
    ]
    if not data["new_listings"]:
        lines.append("_None in this window._")
    else:
        lines.append("| Source | Query | Title | Price | URL | First seen |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for r in data["new_listings"]:
            price = "" if r.get("price") is None else str(r["price"])
            lines.append(
                f"| {r['source']} | {r['query']} | {r['title'][:80]} | {price} | {r['url']} | {r['first_seen']} |"
            )
    lines += ["", "## Removed, sold, or disappeared from SERP", ""]
    if not data["removed"]:
        lines.append("_None in this window._")
    else:
        lines.append("| Source | Query | Title | Last price | Status | Last seen | URL |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for r in data["removed"]:
            price = "" if r.get("price") is None else str(r["price"])
            lines.append(
                f"| {r['source']} | {r['query']} | {str(r['title'])[:60]} | {price} | "
                f"{r['status']} | {r['last_seen']} | {r['url']} |"
            )

    lines += ["", "## Price changes (discounts)", ""]
    if not data["discounts"]:
        lines.append("_None in this window._")
    else:
        lines.append("| Source | Query | Title | Old | New | When | URL |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for r in data["discounts"]:
            lines.append(
                f"| {r['source']} | {r['query']} | {str(r['title'])[:50]} | {r['old_price']} | "
                f"{r['new_price']} | {r['recorded_at']} | {r['url']} |"
            )

    lines += ["", "## Other price changes (increases)", ""]
    if not data["price_increases"]:
        lines.append("_None in this window._")
    else:
        lines.append("| Source | Query | Title | Old | New | When | URL |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for r in data["price_increases"]:
            lines.append(
                f"| {r['source']} | {r['query']} | {str(r['title'])[:50]} | {r['old_price']} | "
                f"{r['new_price']} | {r['recorded_at']} | {r['url']} |"
            )

    lines.append("")
    return "\n".join(lines)


def write_weekly_digest(conn, week_end: date | None = None) -> Path:
    week_end = week_end or date.today()
    data = collect_digest_rows(conn, week_end)
    md = render_weekly_digest_md(data)
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    name = f"weekly_digest_{week_end.strftime('%Y%m%d')}.md"
    path = DIGEST_DIR / name
    path.write_text(md, encoding="utf-8")
    logger.info("Wrote digest %s", path)
    return path


def _load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Furniture competitor monitor (Avito + WB + SQLite).")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scrape = sub.add_parser("scrape", help="Fetch listings and update the database")
    p_scrape.add_argument("--avito", action="append", default=[], metavar="QUERY", help="Avito search phrase (repeatable)")
    p_scrape.add_argument(
        "--avito-region",
        default="all",
        help="Avito region path segment (default: all), e.g. moskva",
    )
    p_scrape.add_argument("--wb", action="append", default=[], metavar="QUERY", help="Wildberries search phrase (repeatable)")
    p_scrape.add_argument("--wb-pages", type=int, default=1, help="WB result pages per query (default: 1)")
    p_scrape.add_argument(
        "--config",
        type=Path,
        help="JSON config with keys avito_queries, wb_queries, avito_region, wb_pages",
    )
    p_scrape.add_argument(
        "--mark-missing-on-empty",
        action="store_true",
        help="If a scrape returns 0 rows, still mark prior active listings as gone (dangerous if blocked).",
    )

    p_digest = sub.add_parser("digest", help="Write weekly digest Markdown for a week ending on a date")
    p_digest.add_argument(
        "--week-end",
        type=str,
        default=date.today().isoformat(),
        help="Week window ends on this date (inclusive), UTC (default: today)",
    )

    p_all = sub.add_parser("run", help="scrape then digest (week ending today)")

    p_all.add_argument("--avito", action="append", default=[], metavar="QUERY")
    p_all.add_argument("--avito-region", default="all")
    p_all.add_argument("--wb", action="append", default=[], metavar="QUERY")
    p_all.add_argument("--wb-pages", type=int, default=1)
    p_all.add_argument("--config", type=Path)
    p_all.add_argument("--mark-missing-on-empty", action="store_true")

    args = parser.parse_args(argv)
    db_path = args.db
    conn = connect_sqlite(db_path)
    init_schema(conn)

    if args.cmd == "digest":
        end = _parse_iso_date(args.week_end)
        path = write_weekly_digest(conn, week_end=end)
        print(path)
        return 0

    avito_specs: list[tuple[str, str]] = []
    wb_specs: list[tuple[str, int]] = []

    if args.cmd in ("scrape", "run"):
        cfg_avito: list[str] = []
        cfg_wb: list[str] = []
        region = args.avito_region
        wb_pages = args.wb_pages
        if getattr(args, "config", None):
            cfg = _load_config(args.config)
            cfg_avito = list(cfg.get("avito_queries") or [])
            cfg_wb = list(cfg.get("wb_queries") or [])
            region = str(cfg.get("avito_region") or region)
            wb_pages = int(cfg.get("wb_pages") or wb_pages)
        for q in list(args.avito) + cfg_avito:
            avito_specs.append((q, region))
        for q in list(args.wb) + cfg_wb:
            wb_specs.append((q, wb_pages))

        if not avito_specs and not wb_specs:
            parser.error("Provide --avito / --wb or a --config with avito_queries / wb_queries")

        session = FurnitureScrapeSession()
        sync_queries(
            conn,
            session,
            avito_specs,
            wb_specs,
            skip_empty_scrape=not args.mark_missing_on_empty,
        )
        if args.cmd == "run":
            path = write_weekly_digest(conn, week_end=date.today())
            print(path)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
