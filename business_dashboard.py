#!/usr/bin/env python3
"""
Unified business dashboard for furniture sales data.

Reads one or more SQLite databases and prints a text dashboard with:
- total sales
- sales by channel
- monthly sales
- top 10 products
- sales by seller

Expected table names:
- offline_sales (required by task spec)
- marketplace_sales
- online_sales
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

CHANNEL_TABLES: tuple[tuple[str, str], ...] = (
    ("offline", "offline_sales"),
    ("marketplace", "marketplace_sales"),
    ("online", "online_sales"),
)


@dataclass
class SaleRecord:
    date: str
    channel: str
    product: str
    quantity: float
    amount: float
    seller: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified furniture business dashboard (console)."
    )
    parser.add_argument(
        "db_paths",
        nargs="+",
        help="One or more SQLite database files.",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=30,
        help="Unicode bar width (default: 30).",
    )
    return parser.parse_args()


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def choose_first_available(columns: set[str], names: Iterable[str]) -> str | None:
    for name in names:
        if name in columns:
            return name
    return None


def load_sales_from_db(db_path: Path) -> list[SaleRecord]:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    records: list[SaleRecord] = []
    conn = sqlite3.connect(str(db_path))
    try:
        for channel, table_name in CHANNEL_TABLES:
            if not has_table(conn, table_name):
                continue

            columns = table_columns(conn, table_name)
            date_col = choose_first_available(columns, ("date", "sale_date", "created_at"))
            product_col = choose_first_available(columns, ("product", "product_name", "item"))
            quantity_col = choose_first_available(columns, ("quantity", "qty", "count"))
            amount_col = choose_first_available(columns, ("amount", "total", "revenue", "sum"))
            seller_col = choose_first_available(
                columns,
                ("seller", "manager", "salesperson", "employee", "point", "store"),
            )

            if not date_col or not product_col or not quantity_col or not amount_col:
                continue

            seller_expr = f"COALESCE({seller_col}, 'N/A')" if seller_col else "'N/A'"
            query = (
                f"SELECT {date_col}, {product_col}, {quantity_col}, {amount_col}, {seller_expr} "
                f"FROM {table_name}"
            )
            for row in conn.execute(query):
                raw_date, product, quantity, amount, seller = row
                records.append(
                    SaleRecord(
                        date=str(raw_date),
                        channel=channel,
                        product=str(product),
                        quantity=float(quantity or 0),
                        amount=float(amount or 0),
                        seller=str(seller or "N/A"),
                    )
                )
    finally:
        conn.close()
    return records


def parse_month(raw_date: str) -> str:
    date_part = raw_date.strip().replace("T", " ").split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_part, fmt).strftime("%Y-%m")
        except ValueError:
            continue
    if len(date_part) >= 7:
        return date_part[:7]
    return "unknown"


def bar(value: float, max_value: float, width: int) -> str:
    if max_value <= 0:
        return " " * width
    filled = int(round((value / max_value) * width))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def format_money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


def print_table_with_chart(
    title: str,
    rows: list[tuple[str, float]],
    width: int,
    value_label: str = "Amount",
) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    if not rows:
        print("No data")
        return
    max_value = max(v for _, v in rows) if rows else 0.0
    for label, value in rows:
        print(f"{label:<20} {format_money(value):>12}  {bar(value, max_value, width)}")
    total = sum(v for _, v in rows)
    print(f"{'Total ' + value_label:<20} {format_money(total):>12}")


def main() -> None:
    args = parse_args()
    db_paths = [Path(p).expanduser() for p in args.db_paths]

    all_records: list[SaleRecord] = []
    for db_path in db_paths:
        all_records.extend(load_sales_from_db(db_path))

    if not all_records:
        print("No sales data found in provided SQLite databases.")
        return

    total_sales = sum(r.amount for r in all_records)
    total_qty = sum(r.quantity for r in all_records)

    by_channel: dict[str, float] = defaultdict(float)
    by_month: dict[str, float] = defaultdict(float)
    by_product: dict[str, float] = defaultdict(float)
    by_seller: dict[str, float] = defaultdict(float)

    for r in all_records:
        by_channel[r.channel] += r.amount
        by_month[parse_month(r.date)] += r.amount
        by_product[r.product] += r.amount
        by_seller[r.seller] += r.amount

    channels_sorted = sorted(by_channel.items(), key=lambda x: x[1], reverse=True)
    months_sorted = sorted(by_month.items(), key=lambda x: x[0])
    products_sorted = sorted(by_product.items(), key=lambda x: x[1], reverse=True)[:10]
    sellers_sorted = sorted(by_seller.items(), key=lambda x: x[1], reverse=True)

    print("=" * 78)
    print("FURNITURE BUSINESS DASHBOARD".center(78))
    print("=" * 78)
    print(f"Records loaded      : {len(all_records)}")
    print(f"Total sales amount  : {format_money(total_sales)}")
    print(f"Total units sold    : {format_money(total_qty)}")
    print(f"Data sources        : {', '.join(str(p) for p in db_paths)}")

    print_table_with_chart("Sales by channel", channels_sorted, args.width)
    print_table_with_chart("Sales by month", months_sorted, args.width)
    print_table_with_chart("Top 10 products", products_sorted, args.width)
    print_table_with_chart("Sales by seller", sellers_sorted, args.width)


if __name__ == "__main__":
    main()
