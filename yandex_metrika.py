"""Yandex Metrika integration for furniture business analytics.

Required capabilities implemented:
- API client for Yandex Metrika
- Counter discovery for amadey.ru and divaninfo.ru
- Traffic statistics retrieval
- Traffic-by-source retrieval
- Conversion rate calculation
- SQLite persistence
- Console table output
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


YANDEX_LOGIN = "Sakura565"
YANDEX_TOKEN = "Nastia56"
YANDEX_API_BASE = "https://api-metrika.yandex.net"
DEFAULT_DB_PATH = "yandex_metrika.db"
TARGET_DOMAINS = ("amadey.ru", "divaninfo.ru")


@dataclass
class CounterInfo:
    """Normalized counter metadata."""

    id: int
    name: str
    site: str


class YandexMetrikaClient:
    """Simple Yandex Metrika API client built on urllib (no extra deps)."""

    def __init__(self, login: str, token: str, timeout: int = 30) -> None:
        self.login = login
        self.token = token
        self.timeout = timeout

    def _request_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{YANDEX_API_BASE}{path}{query}"
        request = urllib.request.Request(
            url=url,
            headers={
                "Authorization": f"OAuth {self.token}",
                "Accept": "application/json",
            },
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_counters(self) -> list[CounterInfo]:
        """Fetch all available counters for the authenticated user."""
        payload = self._request_json("/management/v1/counters", params={"per_page": 1000})
        counters = payload.get("counters", [])
        return [
            CounterInfo(
                id=int(counter["id"]),
                name=str(counter.get("name", "")),
                site=str(counter.get("site", "")),
            )
            for counter in counters
        ]

    def get_counter_for_domain(self, domain: str) -> CounterInfo:
        """Discover counter by matching domain against site URL/host."""
        normalized_domain = domain.strip().lower()
        for counter in self.get_counters():
            site = counter.site.lower()
            parsed = urllib.parse.urlparse(site if "://" in site else f"https://{site}")
            host = parsed.netloc.lower() or parsed.path.lower()
            host = host.removeprefix("www.")
            if normalized_domain == host or normalized_domain in site:
                return counter
        raise ValueError(f"Counter for domain '{domain}' was not found.")

    def get_counter_stats(
        self,
        counter_id: int,
        date1: str,
        date2: str,
    ) -> dict[str, float]:
        """Get traffic metrics for a counter in date range."""
        metrics = [
            "ym:s:visits",
            "ym:s:users",
            "ym:s:pageviews",
            "ym:s:bounceRate",
            "ym:s:avgVisitDurationSeconds",
            "ym:s:goalConversionRate",
        ]
        payload = self._request_json(
            "/stat/v1/data",
            params={
                "ids": counter_id,
                "date1": date1,
                "date2": date2,
                "metrics": ",".join(metrics),
                "accuracy": "full",
            },
        )
        totals = payload.get("totals", [[]])
        values = totals[0] if totals and isinstance(totals[0], list) else []
        normalized = {
            "visits": float(values[0]) if len(values) > 0 else 0.0,
            "users": float(values[1]) if len(values) > 1 else 0.0,
            "pageviews": float(values[2]) if len(values) > 2 else 0.0,
            "bounce_rate": float(values[3]) if len(values) > 3 else 0.0,
            "avg_visit_duration_seconds": float(values[4]) if len(values) > 4 else 0.0,
            "goal_conversion_rate": float(values[5]) if len(values) > 5 else 0.0,
        }
        return normalized

    def get_traffic_by_source(self, counter_id: int, date1: str, date2: str) -> list[dict[str, Any]]:
        """Get visits split by traffic source."""
        payload = self._request_json(
            "/stat/v1/data",
            params={
                "ids": counter_id,
                "date1": date1,
                "date2": date2,
                "dimensions": "ym:s:lastTrafficSource",
                "metrics": "ym:s:visits,ym:s:users,ym:s:bounceRate",
                "sort": "-ym:s:visits",
                "limit": 100,
                "accuracy": "full",
            },
        )
        rows = []
        for row in payload.get("data", []):
            dimension_items = row.get("dimensions", [])
            metrics = row.get("metrics", [])
            rows.append(
                {
                    "source": (
                        str(dimension_items[0].get("name", "unknown"))
                        if dimension_items
                        else "unknown"
                    ),
                    "visits": float(metrics[0]) if len(metrics) > 0 else 0.0,
                    "users": float(metrics[1]) if len(metrics) > 1 else 0.0,
                    "bounce_rate": float(metrics[2]) if len(metrics) > 2 else 0.0,
                }
            )
        return rows


def get_conversion_rate(stats: dict[str, float]) -> float:
    """Return conversion rate (%). Uses goalConversionRate when available."""
    goal_rate = stats.get("goal_conversion_rate", 0.0)
    if goal_rate > 0:
        return round(goal_rate, 2)
    visits = stats.get("visits", 0.0)
    users = stats.get("users", 0.0)
    if visits <= 0:
        return 0.0
    return round((users / visits) * 100, 2)


def save_to_db(
    db_path: str,
    domain: str,
    counter_id: int,
    date1: str,
    date2: str,
    stats: dict[str, float],
) -> None:
    """Create/update traffic table and persist aggregated metrics."""
    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS traffic (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                domain TEXT NOT NULL,
                counter_id INTEGER NOT NULL,
                date1 TEXT NOT NULL,
                date2 TEXT NOT NULL,
                visits REAL NOT NULL,
                users REAL NOT NULL,
                pageviews REAL NOT NULL,
                bounce_rate REAL NOT NULL,
                avg_visit_duration_seconds REAL NOT NULL,
                conversion_rate REAL NOT NULL
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO traffic (
                created_at,
                domain,
                counter_id,
                date1,
                date2,
                visits,
                users,
                pageviews,
                bounce_rate,
                avg_visit_duration_seconds,
                conversion_rate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dt.datetime.utcnow().isoformat(timespec="seconds"),
                domain,
                counter_id,
                date1,
                date2,
                stats.get("visits", 0.0),
                stats.get("users", 0.0),
                stats.get("pageviews", 0.0),
                stats.get("bounce_rate", 0.0),
                stats.get("avg_visit_duration_seconds", 0.0),
                get_conversion_rate(stats),
            ),
        )
        connection.commit()


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))

    def fmt_row(row: list[str]) -> str:
        return " | ".join(value.ljust(widths[idx]) for idx, value in enumerate(row))

    divider = "-+-".join("-" * width for width in widths)
    return "\n".join([fmt_row(headers), divider, *(fmt_row(row) for row in rows)])


def _print_stats_table(domain: str, counter_id: int, stats: dict[str, float]) -> None:
    rows = [
        ["Domain", domain],
        ["Counter ID", str(counter_id)],
        ["Visits", f"{stats.get('visits', 0.0):.0f}"],
        ["Users", f"{stats.get('users', 0.0):.0f}"],
        ["Pageviews", f"{stats.get('pageviews', 0.0):.0f}"],
        ["Bounces, %", f"{stats.get('bounce_rate', 0.0):.2f}"],
        ["Avg visit sec", f"{stats.get('avg_visit_duration_seconds', 0.0):.2f}"],
        ["Conversion, %", f"{get_conversion_rate(stats):.2f}"],
    ]
    print(_format_table(["Metric", "Value"], rows))


def _print_sources_table(domain: str, sources: list[dict[str, Any]]) -> None:
    print(f"\nTraffic sources for {domain}:")
    rows = [
        [
            str(item.get("source", "unknown")),
            f"{item.get('visits', 0.0):.0f}",
            f"{item.get('users', 0.0):.0f}",
            f"{item.get('bounce_rate', 0.0):.2f}",
        ]
        for item in sources
    ]
    if not rows:
        rows = [["no data", "0", "0", "0.00"]]
    print(_format_table(["Source", "Visits", "Users", "Bounce %"], rows))


def run(
    date1: str | None = None,
    date2: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Discover counters for target domains, fetch stats, output and persist."""
    today = dt.date.today()
    if date2 is None:
        date2 = today.isoformat()
    if date1 is None:
        date1 = (today - dt.timedelta(days=7)).isoformat()

    client = YandexMetrikaClient(login=YANDEX_LOGIN, token=YANDEX_TOKEN)
    print(f"Using Yandex login: {client.login}")
    print(f"Date range: {date1} -> {date2}\n")

    for domain in TARGET_DOMAINS:
        print(f"=== {domain} ===")
        try:
            counter = client.get_counter_for_domain(domain)
            stats = client.get_counter_stats(counter.id, date1, date2)
            sources = client.get_traffic_by_source(counter.id, date1, date2)
            save_to_db(
                db_path=db_path,
                domain=domain,
                counter_id=counter.id,
                date1=date1,
                date2=date2,
                stats=stats,
            )
            _print_stats_table(domain, counter.id, stats)
            _print_sources_table(domain, sources)
            print(f"Saved to DB: {db_path}\n")
        except Exception as error:  # pragma: no cover - runtime integration path
            print(f"Failed for {domain}: {error}\n")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 0:
        run()
    elif len(args) == 2:
        run(date1=args[0], date2=args[1])
    elif len(args) == 3:
        run(date1=args[0], date2=args[1], db_path=args[2])
    else:
        print("Usage: python yandex_metrika.py [date1 date2 [db_path]]")
        sys.exit(1)
