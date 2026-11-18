"""Tests for furniture_sales_database.sales_tools.importer (no network, tmp_path DB)."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from furniture_sales_database.sales_tools import importer

FIXTURE_DB = (
    Path(__file__).resolve().parents[1]
    / "furniture_sales_database"
    / "sales_tools"
    / "fixtures"
    / "sales_sample.db"
)
MONTH = "2026-11"
SOURCE_FILE = "1811-1.xlsx"


@pytest.fixture
def sample_db(tmp_path: Path) -> Path:
    db = tmp_path / "sales.db"
    shutil.copy2(FIXTURE_DB, db)
    return db


def _load_month_payload(con: sqlite3.Connection, month: str) -> tuple[dict, list[dict]]:
    cur = con.cursor()
    meta = cur.execute(
        "SELECT period, period_start, period_end, source_file FROM sales WHERE month=? LIMIT 1",
        (month,),
    ).fetchone()
    if not meta:
        raise AssertionError(f"no sales for {month}")
    period_label, p_start, p_end, source_file = meta
    rows = cur.execute(
        "SELECT buyer, item_name, quantity, amount FROM sales WHERE month=? ORDER BY id",
        (month,),
    ).fetchall()
    leaves = [
        {
            "row": i + 1,
            "buyer": r[0],
            "item": r[1],
            "qty": float(r[2]),
            "amount": float(r[3]),
        }
        for i, r in enumerate(rows)
    ]
    buyer_rollups = {
        b: (v["qty"], v["amount"]) for b, v in importer.aggregate_per_buyer(leaves).items()
    }
    lq = sum(x["qty"] for x in leaves)
    ls = sum(x["amount"] for x in leaves)
    parsed = {
        "period_label": period_label,
        "period_start": p_start,
        "period_end": p_end,
        "month": month,
        "grouping_raw": "",
        "filter_raw": "",
        "leaves": leaves,
        "buyer_rollups": buyer_rollups,
        "file_total": (lq, ls),
        "month_rollup": (lq, ls),
    }
    return parsed, leaves, source_file


def _month_totals(con: sqlite3.Connection, month: str) -> tuple[int, float]:
    row = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM sales WHERE month=?",
        (month,),
    ).fetchone()
    return int(row[0]), float(row[1])


def test_slices_migration_idempotent(sample_db: Path) -> None:
    con = sqlite3.connect(sample_db)
    importer.ensure_schema(con)
    n1 = con.execute("SELECT COUNT(*) FROM slices").fetchone()[0]
    importer.ensure_schema(con)
    n2 = con.execute("SELECT COUNT(*) FROM slices").fetchone()[0]
    con.close()
    assert n1 > 0
    assert n1 == n2


def test_reimport_same_slice_idempotent(sample_db: Path) -> None:
    con = sqlite3.connect(sample_db)
    parsed, leaves, source_file = _load_month_payload(con, MONTH)
    rows_before, sum_before = _month_totals(con, MONTH)

    importer.apply_import(con, parsed, leaves, source_file)
    rows_after_one, sum_after_one = _month_totals(con, MONTH)
    slices_after_one = con.execute(
        "SELECT COUNT(*) FROM slices WHERE month=? AND is_current=1", (MONTH,)
    ).fetchone()[0]

    importer.apply_import(con, parsed, leaves, source_file)
    rows_after_two, sum_after_two = _month_totals(con, MONTH)
    slices_after_two = con.execute(
        "SELECT COUNT(*) FROM slices WHERE month=? AND is_current=1", (MONTH,)
    ).fetchone()[0]
    con.close()

    assert rows_before == rows_after_one == rows_after_two
    assert abs(sum_before - sum_after_one) < 0.01
    assert abs(sum_after_one - sum_after_two) < 0.01
    assert slices_after_one == 1
    assert slices_after_two == 1


def test_coverage_regression_aborts_and_keeps_db(sample_db: Path) -> None:
    con = sqlite3.connect(sample_db)
    parsed, leaves, source_file = _load_month_payload(con, MONTH)
    rows_before, sum_before = _month_totals(con, MONTH)

    regressed_buyer = leaves[0]["buyer"]
    shrunk: list[dict] = []
    reduced = False
    for x in leaves:
        copy = dict(x)
        if not reduced and copy["buyer"] == regressed_buyer:
            copy["amount"] = max(0.0, copy["amount"] - 1000.0)
            reduced = True
        shrunk.append(copy)
    buyer_rollups = {
        b: (v["qty"], v["amount"]) for b, v in importer.aggregate_per_buyer(shrunk).items()
    }
    lq = sum(x["qty"] for x in shrunk)
    ls = sum(x["amount"] for x in shrunk)
    bad_parsed = {**parsed, "leaves": shrunk, "buyer_rollups": buyer_rollups, "file_total": (lq, ls)}

    with pytest.raises(importer.SalesImportError, match="РЕГРЕСС"):
        importer.apply_import(con, bad_parsed, shrunk, source_file)

    rows_after, sum_after = _month_totals(con, MONTH)
    con.close()
    assert rows_after == rows_before
    assert abs(sum_after - sum_before) < 0.01


def test_missing_buyer_aborts(sample_db: Path) -> None:
    con = sqlite3.connect(sample_db)
    parsed, leaves, source_file = _load_month_payload(con, MONTH)
    rows_before, sum_before = _month_totals(con, MONTH)

    drop_buyer = leaves[0]["buyer"]
    trimmed = [x for x in leaves if x["buyer"] != drop_buyer]
    buyer_rollups = {
        b: (v["qty"], v["amount"]) for b, v in importer.aggregate_per_buyer(trimmed).items()
    }
    lq = sum(x["qty"] for x in trimmed)
    ls = sum(x["amount"] for x in trimmed)
    bad_parsed = {
        **parsed,
        "leaves": trimmed,
        "buyer_rollups": buyer_rollups,
        "file_total": (lq, ls),
    }

    with pytest.raises(importer.SalesImportError, match="ПОКРЫТИЕ"):
        importer.apply_import(con, bad_parsed, trimmed, source_file)

    rows_after, sum_after = _month_totals(con, MONTH)
    con.close()
    assert rows_after == rows_before
    assert abs(sum_after - sum_before) < 0.01


def test_transaction_rollback_on_insert_failure(sample_db: Path) -> None:
    con = sqlite3.connect(sample_db)
    parsed, leaves, source_file = _load_month_payload(con, MONTH)
    rows_before, sum_before = _month_totals(con, MONTH)

    with pytest.raises(sqlite3.OperationalError, match="simulated"):
        importer.apply_import(con, parsed, leaves, source_file, _fail_after_inserts=3)

    rows_after, sum_after = _month_totals(con, MONTH)
    con.close()
    assert rows_after == rows_before
    assert abs(sum_after - sum_before) < 0.01


def test_buyer_rollup_mismatch_blocks_apply(sample_db: Path) -> None:
    con = sqlite3.connect(sample_db)
    parsed, leaves, _source_file = _load_month_payload(con, MONTH)
    parsed["buyer_rollups"][leaves[0]["buyer"]] = (0.0, 0.0)
    with pytest.raises(importer.SalesImportError, match="ПОКОНТРАГЕНТНАЯ"):
        importer.validate_parsed_data(parsed, leaves)
    con.close()