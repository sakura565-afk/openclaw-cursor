"""Import furniture sales data from XLS into SQLite."""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, Optional

import xlrd

from category_detector import detect_category


HEADER_ALIASES: Dict[str, Iterable[str]] = {
    "sale_date": ("date", "sale_date", "дата", "дата продажи"),
    "product_name": ("product", "product_name", "товар", "наименование"),
    "seller_name": ("seller", "seller_name", "продавец", "менеджер"),
    "platform_name": ("platform", "platform_name", "площадка", "канал"),
    "quantity": ("qty", "quantity", "кол-во", "количество"),
    "unit_price": ("price", "unit_price", "цена", "цена за единицу"),
    "sku": ("sku", "артикул", "код"),
}


def normalize_header(value: object) -> str:
    return str(value or "").strip().lower().replace("ё", "е")


def parse_date(cell_value: object, datemode: int) -> str:
    if cell_value in (None, ""):
        raise ValueError("sale_date is empty")

    if isinstance(cell_value, (int, float)):
        date_obj = xlrd.xldate.xldate_as_datetime(cell_value, datemode)
        return date_obj.date().isoformat()

    value = str(cell_value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unsupported date format: {value}")


def parse_int(value: object, field: str) -> int:
    if value in (None, ""):
        raise ValueError(f"{field} is empty")
    if isinstance(value, (int, float)):
        return int(value)
    return int(str(value).strip())


def parse_float(value: object, field: str) -> float:
    if value in (None, ""):
        raise ValueError(f"{field} is empty")
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).strip().replace(",", "."))


def resolve_headers(header_row: Iterable[object]) -> Dict[str, int]:
    normalized_to_index: Dict[str, int] = {
        normalize_header(cell): idx for idx, cell in enumerate(header_row)
    }
    resolved: Dict[str, int] = {}
    required = ("sale_date", "product_name", "seller_name", "platform_name", "quantity", "unit_price")

    for logical_name, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            if alias in normalized_to_index:
                resolved[logical_name] = normalized_to_index[alias]
                break

    missing = [name for name in required if name not in resolved]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")
    return resolved


def get_or_create(cur: sqlite3.Cursor, table: str, name: str) -> int:
    cur.execute(f"SELECT id FROM {table} WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return int(row[0])
    cur.execute(f"INSERT INTO {table}(name) VALUES (?)", (name,))
    return int(cur.lastrowid)


def get_or_create_product(
    cur: sqlite3.Cursor, *, sku: Optional[str], name: str, category: str
) -> int:
    if sku:
        cur.execute("SELECT id FROM products WHERE sku = ?", (sku,))
        row = cur.fetchone()
        if row:
            return int(row[0])

    cur.execute("SELECT id FROM products WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return int(row[0])

    cur.execute(
        "INSERT INTO products(sku, name, category) VALUES (?, ?, ?)",
        (sku, name, category),
    )
    return int(cur.lastrowid)


def initialize_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    conn.executescript(schema_path.read_text(encoding="utf-8"))


def import_xls(db_path: Path, xls_path: Path, schema_path: Path) -> None:
    wb = xlrd.open_workbook(str(xls_path))
    sheet = wb.sheet_by_index(0)
    if sheet.nrows < 2:
        raise ValueError("XLS file has no data rows")

    headers = resolve_headers(sheet.row_values(0))

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        initialize_schema(conn, schema_path)
        cur = conn.cursor()
        imported = 0

        for row_idx in range(1, sheet.nrows):
            values = sheet.row_values(row_idx)
            if not any(str(v).strip() for v in values):
                continue

            sale_date = parse_date(values[headers["sale_date"]], wb.datemode)
            product_name = str(values[headers["product_name"]]).strip()
            seller_name = str(values[headers["seller_name"]]).strip()
            platform_name = str(values[headers["platform_name"]]).strip()
            quantity = parse_int(values[headers["quantity"]], "quantity")
            unit_price = parse_float(values[headers["unit_price"]], "unit_price")
            sku = None
            if "sku" in headers:
                raw_sku = str(values[headers["sku"]]).strip()
                sku = raw_sku or None

            category = detect_category(product_name)
            product_id = get_or_create_product(cur, sku=sku, name=product_name, category=category)
            seller_id = get_or_create(cur, "sellers", seller_name)
            platform_id = get_or_create(cur, "platforms", platform_name)
            total_amount = round(quantity * unit_price, 2)

            cur.execute(
                """
                INSERT INTO sales(
                    sale_date, product_id, seller_id, platform_id,
                    quantity, unit_price, total_amount, source_row
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sale_date,
                    product_id,
                    seller_id,
                    platform_id,
                    quantity,
                    unit_price,
                    total_amount,
                    row_idx + 1,
                ),
            )
            imported += 1

        conn.commit()
        print(f"Import complete. Rows imported: {imported}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import furniture sales XLS into SQLite DB.")
    parser.add_argument("--db", default="furniture_sales.db", help="Path to SQLite database file.")
    parser.add_argument("--xls", required=True, help="Path to source .xls file.")
    parser.add_argument(
        "--schema",
        default="schema.sql",
        help="Path to schema SQL file.",
    )
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    xls_path = Path(args.xls).resolve()
    schema_path = Path(args.schema).resolve()

    if not xls_path.exists():
        raise FileNotFoundError(f"XLS file not found: {xls_path}")
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")

    import_xls(db_path=db_path, xls_path=xls_path, schema_path=schema_path)


if __name__ == "__main__":
    main()
