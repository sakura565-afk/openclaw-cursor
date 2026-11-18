"""Tests for flat -> normalized sales migration."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from furniture_sales_database.sales_tools.migration import (
    migrate_flat_sales,
    table_row_counts,
    verify_migration,
)

FIXTURE_DB = (
    Path(__file__).resolve().parents[1]
    / "furniture_sales_database"
    / "sales_tools"
    / "fixtures"
    / "sales_sample.db"
)


@pytest.fixture
def flat_copy(tmp_path: Path) -> Path:
    """Writable copy of the anonymized flat fixture."""
    dest = tmp_path / "flat_sales.db"
    shutil.copy(FIXTURE_DB, dest)
    return dest


@pytest.fixture
def target_db(tmp_path: Path) -> Path:
    return tmp_path / "normalized_sales.db"


def test_migration_totals_and_products(flat_copy: Path, target_db: Path) -> None:
    migrate_flat_sales(flat_copy, target_db)
    verify_migration(flat_copy, target_db)

    with sqlite3.connect(flat_copy) as src, sqlite3.connect(target_db) as dst:
        flat_distinct = src.execute("SELECT COUNT(DISTINCT item_name) FROM sales").fetchone()[0]
        norm_products = dst.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        assert flat_distinct == norm_products

        null_refs = dst.execute(
            "SELECT COUNT(*) FROM sales WHERE channel_id IS NULL OR seller_id IS NULL"
        ).fetchone()[0]
        assert null_refs == 0

        sale_count = dst.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        flat_count = src.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        assert sale_count == flat_count


def test_migration_idempotent(flat_copy: Path, target_db: Path) -> None:
    migrate_flat_sales(flat_copy, target_db)
    counts_after_first = table_row_counts(target_db)

    migrate_flat_sales(flat_copy, target_db)
    counts_after_second = table_row_counts(target_db)

    assert counts_after_first == counts_after_second


def test_monthly_sums_match_within_cent(flat_copy: Path, target_db: Path) -> None:
    migrate_flat_sales(flat_copy, target_db)
    with sqlite3.connect(flat_copy) as src, sqlite3.connect(target_db) as dst:
        flat = {
            row[0]: float(row[1])
            for row in src.execute(
                "SELECT month, SUM(amount) FROM sales GROUP BY month"
            )
        }
        norm = {
            row[0]: float(row[1])
            for row in dst.execute(
                "SELECT month, SUM(total_amount) FROM sales GROUP BY month"
            )
        }
        assert set(flat) == set(norm)
        for month in flat:
            assert abs(flat[month] - norm[month]) <= 0.01


def test_channel_semantics(flat_copy: Path, target_db: Path) -> None:
    migrate_flat_sales(flat_copy, target_db)
    with sqlite3.connect(flat_copy) as src, sqlite3.connect(target_db) as dst:
        flat_salon_amt = src.execute(
            """
            SELECT COALESCE(SUM(amount), 0) FROM sales
            WHERE salon IS NOT NULL AND TRIM(salon) != ''
            """
        ).fetchone()[0]
        norm_salon_amt = dst.execute(
            """
            SELECT COALESCE(SUM(s.total_amount), 0)
            FROM sales s
            JOIN channels c ON c.id = s.channel_id
            WHERE c.kind = 'salon'
            """
        ).fetchone()[0]
        assert abs(float(flat_salon_amt) - float(norm_salon_amt)) <= 0.01

        cp_names = {row[0] for row in dst.execute("SELECT name FROM counterparties")}
        salon_names = {row[0] for row in dst.execute("SELECT name FROM channels WHERE kind = 'salon'")}
        assert not cp_names & salon_names

        flat_salon_count = src.execute(
            """
            SELECT COUNT(DISTINCT salon) FROM sales
            WHERE salon IS NOT NULL AND TRIM(salon) != ''
            """
        ).fetchone()[0]
        norm_salon_count = dst.execute(
            "SELECT COUNT(*) FROM channels WHERE kind = 'salon'"
        ).fetchone()[0]
        assert flat_salon_count == norm_salon_count

        flat_total = src.execute("SELECT COALESCE(SUM(amount), 0) FROM sales").fetchone()[0]
        norm_total = dst.execute(
            """
            SELECT COALESCE(SUM(s.total_amount), 0)
            FROM sales s
            JOIN channels c ON c.id = s.channel_id
            """
        ).fetchone()[0]
        assert abs(float(flat_total) - float(norm_total)) <= 0.01