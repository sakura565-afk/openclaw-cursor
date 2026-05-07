"""Marketplace analytics dashboard for Amadey.ru, Wildberries, and Ozon.

This script uses SQLite to store marketplace sales and renders analytics
in text-based tables and charts for terminal usage.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence


@dataclass(frozen=True)
class SaleRecord:
    """Single sales event row."""

    tx_date: str
    marketplace: str
    product: str
    category: str
    quantity: int
    amount: float
    returns: int


SAMPLE_DATA: Sequence[SaleRecord] = (
    SaleRecord("2026-01-04", "Amadey.ru", "Silk Dress", "Apparel", 8, 15600.0, 1),
    SaleRecord("2026-01-09", "Wildberries", "Silk Dress", "Apparel", 16, 29600.0, 2),
    SaleRecord("2026-01-16", "Ozon", "Silk Dress", "Apparel", 11, 20100.0, 1),
    SaleRecord("2026-02-03", "Amadey.ru", "Leather Belt", "Accessories", 20, 18000.0, 1),
    SaleRecord("2026-02-06", "Wildberries", "Leather Belt", "Accessories", 35, 31500.0, 3),
    SaleRecord("2026-02-14", "Ozon", "Leather Belt", "Accessories", 29, 26100.0, 2),
    SaleRecord("2026-03-01", "Amadey.ru", "Classic Shirt", "Apparel", 12, 14400.0, 1),
    SaleRecord("2026-03-07", "Wildberries", "Classic Shirt", "Apparel", 25, 28750.0, 2),
    SaleRecord("2026-03-18", "Ozon", "Classic Shirt", "Apparel", 18, 20700.0, 1),
    SaleRecord("2026-04-05", "Amadey.ru", "Travel Bag", "Bags", 10, 25000.0, 1),
    SaleRecord("2026-04-10", "Wildberries", "Travel Bag", "Bags", 19, 47500.0, 2),
    SaleRecord("2026-04-21", "Ozon", "Travel Bag", "Bags", 14, 35000.0, 1),
    SaleRecord("2026-05-03", "Amadey.ru", "Sneakers", "Shoes", 13, 28600.0, 1),
    SaleRecord("2026-05-12", "Wildberries", "Sneakers", "Shoes", 28, 61600.0, 3),
    SaleRecord("2026-05-19", "Ozon", "Sneakers", "Shoes", 21, 46200.0, 2),
)


def setup_database(connection: sqlite3.Connection) -> None:
    """Create required marketplace_sales table."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS marketplace_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            marketplace TEXT NOT NULL,
            product TEXT NOT NULL,
            category TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            amount REAL NOT NULL,
            returns INTEGER NOT NULL
        )
        """
    )
    connection.commit()


def seed_if_empty(connection: sqlite3.Connection) -> None:
    """Insert demo data for dashboard display when table is empty."""
    existing = connection.execute("SELECT COUNT(*) FROM marketplace_sales").fetchone()[0]
    if existing:
        return
    connection.executemany(
        """
        INSERT INTO marketplace_sales (date, marketplace, product, category, quantity, amount, returns)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (r.tx_date, r.marketplace, r.product, r.category, r.quantity, r.amount, r.returns)
            for r in SAMPLE_DATA
        ],
    )
    connection.commit()


def money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


def render_table(title: str, headers: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    rows = [list(map(str, row)) for row in rows]
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    top = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    bottom = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def line(cells: Sequence[str]) -> str:
        return "| " + " | ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(cells)) + " |"

    output = [title, top, line(headers), sep]
    output.extend(line(row) for row in rows)
    output.append(bottom)
    return "\n".join(output)


def bar_chart(title: str, datapoints: Sequence[tuple[str, float]], width: int = 40) -> str:
    if not datapoints:
        return f"{title}\n(no data)"
    peak = max(value for _, value in datapoints) or 1.0
    lines = [title]
    for label, value in datapoints:
        size = int((value / peak) * width)
        bar = "#" * max(1, size) if value > 0 else ""
        lines.append(f"{label:>10} | {bar} {money(value)}")
    return "\n".join(lines)


def summary_metrics(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        """
        SELECT
            COALESCE(SUM(amount), 0),
            COALESCE(SUM(returns), 0),
            COALESCE(SUM(quantity), 0),
            COUNT(*)
        FROM marketplace_sales
        """
    ).fetchone()
    total_sales, total_returns, total_qty, checks = row
    avg_check = (total_sales / checks) if checks else 0.0
    rows = [
        ("Total sales", money(total_sales)),
        ("Total returns", str(total_returns)),
        ("Total items sold", str(total_qty)),
        ("Average check", money(avg_check)),
    ]
    return render_table("Marketplace KPI Summary", ("Metric", "Value"), rows)


def top_products(connection: sqlite3.Connection, limit: int = 5) -> str:
    rows = connection.execute(
        """
        SELECT product, category, SUM(quantity) AS qty, SUM(amount) AS revenue
        FROM marketplace_sales
        GROUP BY product, category
        ORDER BY revenue DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    formatted = [(p, c, str(q), money(a)) for p, c, q, a in rows]
    return render_table("Top Products by Revenue", ("Product", "Category", "Qty", "Revenue"), formatted)


def monthly_dynamics(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """
        SELECT SUBSTR(date, 1, 7) AS month, SUM(amount) AS revenue
        FROM marketplace_sales
        GROUP BY month
        ORDER BY month
        """
    ).fetchall()
    points = [(month, revenue) for month, revenue in rows]
    table = render_table(
        "Monthly Dynamics",
        ("Month", "Revenue"),
        [(m, money(v)) for m, v in points],
    )
    chart = bar_chart("Monthly Revenue Chart", points)
    return f"{table}\n\n{chart}"


def channel_comparison(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """
        SELECT
            marketplace,
            SUM(amount) AS sales,
            SUM(returns) AS returns,
            COUNT(*) AS checks
        FROM marketplace_sales
        WHERE marketplace IN ('Amadey.ru', 'Wildberries', 'Ozon')
        GROUP BY marketplace
        ORDER BY sales DESC
        """
    ).fetchall()
    table_rows = []
    chart_points = []
    for marketplace, sales, returns, checks in rows:
        avg_check = sales / checks if checks else 0
        table_rows.append((marketplace, money(sales), str(returns), money(avg_check)))
        chart_points.append((marketplace, sales))
    table = render_table(
        "Channel Comparison: Amadey.ru vs WB vs Ozon",
        ("Channel", "Sales", "Returns", "Avg check"),
        table_rows,
    )
    chart = bar_chart("Sales by Channel", chart_points)
    return f"{table}\n\n{chart}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Marketplace analytics dashboard (WB + Ozon + Amadey.ru).")
    parser.add_argument(
        "--db-path",
        default="marketplace_analytics.db",
        help="Path to SQLite database file.",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Seed example data when table is empty.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear existing marketplace_sales data before running.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with sqlite3.connect(args.db_path) as connection:
        setup_database(connection)
        if args.reset:
            connection.execute("DELETE FROM marketplace_sales")
            connection.commit()
        if args.seed:
            seed_if_empty(connection)

        print(f"Marketplace Analytics Dashboard — {date.today().isoformat()}")
        print(summary_metrics(connection))
        print()
        print(top_products(connection))
        print()
        print(monthly_dynamics(connection))
        print()
        print(channel_comparison(connection))


if __name__ == "__main__":
    main()
