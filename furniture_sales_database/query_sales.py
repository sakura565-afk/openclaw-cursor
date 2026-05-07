"""Example analytics queries for furniture sales database."""

from __future__ import annotations

import argparse
import sqlite3
from typing import Iterable, Sequence


def print_rows(title: str, columns: Sequence[str], rows: Iterable[sqlite3.Row]) -> None:
    print(f"\n=== {title} ===")
    print(" | ".join(columns))
    for row in rows:
        print(" | ".join(str(row[col]) for col in columns))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run example queries for furniture sales DB.")
    parser.add_argument("--db", default="furniture_sales.db", help="Path to SQLite database file.")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT p.category, ROUND(SUM(s.total_amount), 2) AS revenue
            FROM sales s
            JOIN products p ON p.id = s.product_id
            GROUP BY p.category
            ORDER BY revenue DESC
            """
        )
        print_rows("Revenue by category", ("category", "revenue"), cur.fetchall())

        cur.execute(
            """
            SELECT sel.name AS seller, ROUND(SUM(s.total_amount), 2) AS revenue
            FROM sales s
            JOIN sellers sel ON sel.id = s.seller_id
            GROUP BY sel.id
            ORDER BY revenue DESC
            LIMIT 5
            """
        )
        print_rows("Top 5 sellers by revenue", ("seller", "revenue"), cur.fetchall())

        cur.execute(
            """
            SELECT pl.name AS platform, SUM(s.quantity) AS units_sold
            FROM sales s
            JOIN platforms pl ON pl.id = s.platform_id
            GROUP BY pl.id
            ORDER BY units_sold DESC
            """
        )
        print_rows("Units sold by platform", ("platform", "units_sold"), cur.fetchall())

        cur.execute(
            """
            SELECT sale_date, ROUND(SUM(total_amount), 2) AS daily_revenue
            FROM sales
            GROUP BY sale_date
            ORDER BY sale_date
            """
        )
        print_rows("Daily revenue", ("sale_date", "daily_revenue"), cur.fetchall())

    finally:
        conn.close()


if __name__ == "__main__":
    main()
