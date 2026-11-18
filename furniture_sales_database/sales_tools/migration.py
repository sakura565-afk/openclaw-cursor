"""Migrate flat sales.db schema into the normalized sales schema."""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_SCHEMA_PATH = Path(__file__).with_name("schema_normalized.sql")
_AMOUNT_TOLERANCE = 0.01


class MigrationVerificationError(RuntimeError):
    """Raised when post-migration totals diverge from the flat source."""


@dataclass(frozen=True)
class MigrationStats:
    """Row counts inserted on the last migrate_flat_sales call."""

    sales_inserted: int
    sales_skipped: int


def _connect(path: Path | str, *, read_only: bool = False) -> sqlite3.Connection:
    uri = f"file:{Path(path).resolve()}?mode=ro" if read_only else str(path)
    if read_only:
        con = sqlite3.connect(uri, uri=True)
    else:
        con = sqlite3.connect(uri)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def ensure_normalized_schema(target_db: Path | str) -> None:
    """Create normalized tables in ``target_db`` if they are missing."""
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    with _connect(target_db) as con:
        con.executescript(sql)
        con.commit()


def copy_suppliers_from_legacy(legacy_db: Path | str, target_db: Path | str) -> int:
    """Copy supplier rows from an old normalized database (best-effort)."""
    legacy_path = Path(legacy_db)
    if not legacy_path.is_file():
        return 0
    inserted = 0
    with _connect(legacy_path, read_only=True) as src, _connect(target_db) as dst:
        rows = src.execute(
            "SELECT name, contact, phone, email, website, address, notes, created_at FROM suppliers"
        ).fetchall()
        for row in rows:
            cur = dst.execute(
                """
                INSERT OR IGNORE INTO suppliers
                    (name, contact, phone, email, website, address, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["name"],
                    row["contact"],
                    row["phone"],
                    row["email"],
                    row["website"],
                    row["address"],
                    row["notes"],
                    row["created_at"],
                ),
            )
            inserted += cur.rowcount
        dst.commit()
    return inserted


def _channel_kind(buyer_type: str | None, salon: str | None, channel_type: str | None) -> str:
    if buyer_type == "salon":
        return "salon"
    if salon:
        return "salon"
    if buyer_type == "manager" and channel_type in ("online", "mixed"):
        return "platform"
    if buyer_type == "supplier":
        return "platform"
    return "salon"


def _flat_channel_name(row: Mapping[str, Any]) -> str:
    salon = row["salon"]
    buyer = row["buyer"]
    if salon:
        return str(salon)
    return str(buyer)


def _flat_seller_name(row: Mapping[str, Any]) -> str:
    seller = row["seller"]
    if seller:
        return str(seller)
    buyer_type = row["buyer_type"]
    buyer = row["buyer"]
    if buyer_type in ("manager", "salon", "supplier"):
        return str(buyer)
    return "(не указан)"


def _flat_counterparty_name(row: Mapping[str, Any]) -> str | None:
    if row["buyer_type"] == "ip":
        return str(row["buyer"])
    return None


def _unit_price(quantity: float, amount: float) -> float:
    if abs(quantity) < 1e-12:
        return 0.0
    return amount / quantity


def _sale_date(row: Mapping[str, Any]) -> str:
    if row["period_end"]:
        return str(row["period_end"])
    if row["period_start"]:
        return str(row["period_start"])
    month = row["month"]
    if month:
        return f"{month}-01"
    return "1970-01-01"


class _Registry:
    def __init__(self, con: sqlite3.Connection, buyers: dict[tuple[str, str | None], sqlite3.Row]) -> None:
        self._con = con
        self._buyers = buyers
        self._products: dict[str, int] = {}
        self._sellers: dict[str, int] = {}
        self._channels: dict[tuple[str, str], int] = {}
        self._counterparties: dict[str, int] = {}

    def product_id(self, name: str, parent_category: str | None) -> int:
        if name in self._products:
            return self._products[name]
        row = self._con.execute("SELECT id FROM products WHERE name = ?", (name,)).fetchone()
        if row:
            self._products[name] = int(row["id"])
            return self._products[name]
        category = (parent_category or "").strip()
        cur = self._con.execute(
            "INSERT INTO products (name, category) VALUES (?, ?)",
            (name, category),
        )
        pid = int(cur.lastrowid)
        self._products[name] = pid
        return pid

    def seller_id(self, name: str, buyer_type: str | None) -> int:
        if name in self._sellers:
            return self._sellers[name]
        row = self._con.execute("SELECT id FROM sellers WHERE name = ?", (name,)).fetchone()
        if row:
            self._sellers[name] = int(row["id"])
            return self._sellers[name]
        channel_type = None
        for (bname, btype), brow in self._buyers.items():
            if bname == name and btype == buyer_type:
                channel_type = brow["channel_type"]
                break
        cur = self._con.execute(
            "INSERT INTO sellers (name, buyer_type, channel_type) VALUES (?, ?, ?)",
            (name, buyer_type, channel_type),
        )
        sid = int(cur.lastrowid)
        self._sellers[name] = sid
        return sid

    def channel_id(self, name: str, kind: str, channel_type: str | None) -> int:
        key = (name, kind)
        if key in self._channels:
            return self._channels[key]
        row = self._con.execute(
            "SELECT id FROM channels WHERE name = ? AND kind = ?",
            (name, kind),
        ).fetchone()
        if row:
            self._channels[key] = int(row["id"])
            return self._channels[key]
        cur = self._con.execute(
            "INSERT INTO channels (name, kind, channel_type) VALUES (?, ?, ?)",
            (name, kind, channel_type),
        )
        cid = int(cur.lastrowid)
        self._channels[key] = cid
        return cid

    def counterparty_id(self, name: str) -> int:
        if name in self._counterparties:
            return self._counterparties[name]
        row = self._con.execute("SELECT id FROM counterparties WHERE name = ?", (name,)).fetchone()
        if row:
            self._counterparties[name] = int(row["id"])
            return self._counterparties[name]
        brow = self._buyers.get((name, "ip"))
        cur = self._con.execute(
            "INSERT INTO counterparties (name, buyer_type, channel_type) VALUES (?, ?, ?)",
            (name, "ip", brow["channel_type"] if brow else None),
        )
        cid = int(cur.lastrowid)
        self._counterparties[name] = cid
        return cid


def _load_buyers(con: sqlite3.Connection) -> dict[tuple[str, str | None], sqlite3.Row]:
    out: dict[tuple[str, str | None], sqlite3.Row] = {}
    for row in con.execute("SELECT name, buyer_type, channel_type FROM buyers"):
        out[(row["name"], row["buyer_type"])] = row
    return out


def _seed_sellers_and_channels_from_buyers(con: sqlite3.Connection, buyers: dict[tuple[str, str | None], sqlite3.Row]) -> None:
    for (name, buyer_type), row in buyers.items():
        if buyer_type in ("manager", "salon", "supplier"):
            con.execute(
                "INSERT OR IGNORE INTO sellers (name, buyer_type, channel_type) VALUES (?, ?, ?)",
                (name, buyer_type, row["channel_type"]),
            )
        if buyer_type == "ip":
            con.execute(
                "INSERT OR IGNORE INTO counterparties (name, buyer_type, channel_type) VALUES (?, ?, ?)",
                (name, buyer_type, row["channel_type"]),
            )
        if buyer_type in ("salon", "manager"):
            kind = _channel_kind(buyer_type, name if buyer_type == "salon" else None, row["channel_type"])
            con.execute(
                "INSERT OR IGNORE INTO channels (name, kind, channel_type) VALUES (?, ?, ?)",
                (name, kind, row["channel_type"]),
            )
    con.execute(
        "INSERT OR IGNORE INTO sellers (name, buyer_type, channel_type) VALUES (?, ?, ?)",
        ("(не указан)", None, None),
    )


def migrate_flat_sales(
    source_db: Path | str,
    target_db: Path | str,
    *,
    legacy_db: Path | str | None = None,
) -> MigrationStats:
    """Copy flat ``sales`` rows into ``target_db`` using get-or-create dimensions."""
    ensure_normalized_schema(target_db)
    if legacy_db:
        copy_suppliers_from_legacy(legacy_db, target_db)

    inserted = 0
    skipped = 0
    with _connect(source_db, read_only=True) as src, _connect(target_db) as dst:
        buyers = _load_buyers(src)
        _seed_sellers_and_channels_from_buyers(dst, buyers)
        reg = _Registry(dst, buyers)

        sales_rows = src.execute(
            """
            SELECT id, period, period_start, period_end, buyer, month, parent_category,
                   item_name, quantity, amount, source_file, loaded_at, buyer_type,
                   seller, salon, item_characteristic, is_return, report_filter
            FROM sales
            ORDER BY id
            """
        ).fetchall()

        insert_sql = """
            INSERT OR IGNORE INTO sales (
                sale_date, period, period_start, period_end, month,
                product_id, seller_id, channel_id, counterparty_id,
                quantity, unit_price, total_amount,
                parent_category, item_characteristic,
                source_file, report_filter, is_return,
                source_flat_id, loaded_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """

        for row in sales_rows:
            flat_id = int(row["id"])
            exists = dst.execute(
                "SELECT 1 FROM sales WHERE source_flat_id = ?",
                (flat_id,),
            ).fetchone()
            if exists:
                skipped += 1
                continue

            buyer_type = row["buyer_type"]
            brow = buyers.get((row["buyer"], buyer_type))
            channel_type = brow["channel_type"] if brow else None

            channel_name = _flat_channel_name(row)
            kind = _channel_kind(buyer_type, row["salon"], channel_type)
            seller_name = _flat_seller_name(row)
            seller_bt = buyer_type if seller_name == row["buyer"] else None
            if seller_name == "(не указан)":
                seller_bt = None

            product_id = reg.product_id(str(row["item_name"]), row["parent_category"])
            seller_id = reg.seller_id(seller_name, seller_bt)
            channel_id = reg.channel_id(channel_name, kind, channel_type)

            counterparty_id: int | None = None
            cname = _flat_counterparty_name(row)
            if cname:
                counterparty_id = reg.counterparty_id(cname)

            qty = float(row["quantity"])
            amount = float(row["amount"])
            cur = dst.execute(
                insert_sql,
                (
                    _sale_date(row),
                    row["period"],
                    row["period_start"],
                    row["period_end"],
                    row["month"],
                    product_id,
                    seller_id,
                    channel_id,
                    counterparty_id,
                    qty,
                    _unit_price(qty, amount),
                    amount,
                    row["parent_category"],
                    row["item_characteristic"],
                    row["source_file"],
                    row["report_filter"],
                    int(row["is_return"] or 0),
                    flat_id,
                    row["loaded_at"],
                ),
            )
            if cur.rowcount:
                inserted += 1
            else:
                skipped += 1

        dst.commit()

    return MigrationStats(sales_inserted=inserted, sales_skipped=skipped)


def _totals_close(a: float, b: float, *, tol: float = _AMOUNT_TOLERANCE) -> bool:
    return abs(a - b) <= tol


def _monthly_totals(con: sqlite3.Connection, *, normalized: bool) -> dict[str | None, float]:
    if normalized:
        sql = "SELECT month, COALESCE(SUM(total_amount), 0) FROM sales GROUP BY month"
    else:
        sql = "SELECT month, COALESCE(SUM(amount), 0) FROM sales GROUP BY month"
    return {row[0]: float(row[1]) for row in con.execute(sql)}


def _channel_totals(con: sqlite3.Connection, *, normalized: bool) -> dict[str, float]:
    if normalized:
        sql = """
            SELECT c.name, COALESCE(SUM(s.total_amount), 0)
            FROM sales s
            JOIN channels c ON c.id = s.channel_id
            GROUP BY c.name
        """
        return {row[0]: float(row[1]) for row in con.execute(sql)}
    sql = """
        SELECT COALESCE(salon, buyer) AS ch, COALESCE(SUM(amount), 0)
        FROM sales
        GROUP BY ch
    """
    return {row[0]: float(row[1]) for row in con.execute(sql)}


def _counterparty_totals(con: sqlite3.Connection, *, normalized: bool) -> dict[str, float]:
    if normalized:
        sql = """
            SELECT cp.name, COALESCE(SUM(s.total_amount), 0)
            FROM sales s
            JOIN counterparties cp ON cp.id = s.counterparty_id
            GROUP BY cp.name
        """
        return {row[0]: float(row[1]) for row in con.execute(sql)}
    sql = """
        SELECT buyer, COALESCE(SUM(amount), 0)
        FROM sales
        WHERE buyer_type = 'ip'
        GROUP BY buyer
    """
    return {row[0]: float(row[1]) for row in con.execute(sql)}


def verify_migration(source_db: Path | str, target_db: Path | str) -> None:
    """Ensure normalized totals match the flat database."""
    errors: list[str] = []
    with _connect(source_db, read_only=True) as src, _connect(target_db, read_only=True) as dst:
        flat_months = _monthly_totals(src, normalized=False)
        norm_months = _monthly_totals(dst, normalized=True)
        months = set(flat_months) | set(norm_months)
        for month in sorted(months, key=lambda m: (m is None, m or "")):
            fa = flat_months.get(month, 0.0)
            na = norm_months.get(month, 0.0)
            if not _totals_close(fa, na):
                errors.append(f"month {month!r}: flat={fa:.2f} normalized={na:.2f}")

        flat_ch = _channel_totals(src, normalized=False)
        norm_ch = _channel_totals(dst, normalized=True)
        for name in sorted(set(flat_ch) | set(norm_ch)):
            fa = flat_ch.get(name, 0.0)
            na = norm_ch.get(name, 0.0)
            if not _totals_close(fa, na):
                errors.append(f"channel {name!r}: flat={fa:.2f} normalized={na:.2f}")

        flat_cp = _counterparty_totals(src, normalized=False)
        norm_cp = _counterparty_totals(dst, normalized=True)
        for name in sorted(set(flat_cp) | set(norm_cp)):
            fa = flat_cp.get(name, 0.0)
            na = norm_cp.get(name, 0.0)
            if not _totals_close(fa, na):
                errors.append(f"counterparty {name!r}: flat={fa:.2f} normalized={na:.2f}")

        flat_products = src.execute("SELECT COUNT(DISTINCT item_name) FROM sales").fetchone()[0]
        norm_products = dst.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if int(flat_products) != int(norm_products):
            errors.append(f"products: flat distinct={flat_products} normalized={norm_products}")

        null_refs = dst.execute(
            "SELECT COUNT(*) FROM sales WHERE channel_id IS NULL OR seller_id IS NULL"
        ).fetchone()[0]
        if null_refs:
            errors.append(f"sales with NULL channel_id or seller_id: {null_refs}")

    if errors:
        raise MigrationVerificationError("; ".join(errors))


def table_row_counts(target_db: Path | str) -> dict[str, int]:
    """Return row counts for all user tables in the normalized database."""
    tables = (
        "suppliers",
        "products",
        "sellers",
        "channels",
        "counterparties",
        "sales",
    )
    out: dict[str, int] = {}
    with _connect(target_db, read_only=True) as con:
        for table in tables:
            out[table] = int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate flat sales.db to normalized schema.")
    parser.add_argument("--source", required=True, help="Path to flat SQLite database.")
    parser.add_argument("--target", required=True, help="Path to normalized SQLite database.")
    parser.add_argument("--legacy", default=None, help="Optional legacy normalized DB (suppliers).")
    parser.add_argument("--verify", action="store_true", help="Run verification after migration.")
    args = parser.parse_args()

    stats = migrate_flat_sales(args.source, args.target, legacy_db=args.legacy)
    print(f"inserted={stats.sales_inserted} skipped={stats.sales_skipped}")
    if args.verify:
        verify_migration(args.source, args.target)
        print("verification: OK")


if __name__ == "__main__":
    main()