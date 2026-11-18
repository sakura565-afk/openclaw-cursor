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


FIXTURE_SALONS_NOV = ("Салон Запад", "Салон Север", "Салон Центр")
NOV_ROW_COUNT = 112
NOV_QTY = 268.0
NOV_AMOUNT = 5_766_389.0

# Фрагменты реальных имён — в исходнике importer их быть не должно (тест, не данные).
_FORBIDDEN_IN_IMPORTER_SOURCE = (
    "Марьино",
    "Румер",
    "Гранд",
    "Михайлов",
    "Тюменцева",
)


def test_salons_backfill_from_fixture(sample_db: Path) -> None:
    con = sqlite3.connect(sample_db)
    importer.ensure_schema(con)
    names = {
        r[0]
        for r in con.execute(
            "SELECT name FROM salons WHERE is_active=1"
        ).fetchall()
    }
    con.close()
    for salon in FIXTURE_SALONS_NOV:
        assert salon in names


def test_november_reimport_preserves_totals_and_types(sample_db: Path) -> None:
    con = sqlite3.connect(sample_db)
    parsed, leaves, source_file = _load_month_payload(con, MONTH)
    importer.apply_import(con, parsed, leaves, source_file)

    row_cnt, qty, amt = con.execute(
        "SELECT COUNT(*), ROUND(SUM(quantity),2), ROUND(SUM(amount),2) FROM sales WHERE month=?",
        (MONTH,),
    ).fetchone()
    by_type = dict(
        con.execute(
            "SELECT buyer_type, COUNT(*) FROM sales WHERE month=? GROUP BY buyer_type",
            (MONTH,),
        ).fetchall()
    )
    salon_buyers = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT buyer FROM sales WHERE month=? AND buyer_type='salon'",
            (MONTH,),
        ).fetchall()
    ]
    con.close()

    assert row_cnt == NOV_ROW_COUNT
    assert float(qty) == NOV_QTY
    assert float(amt) == NOV_AMOUNT
    assert by_type.get("salon") == 100
    assert by_type.get("ip") == 12
    assert set(salon_buyers) == set(FIXTURE_SALONS_NOV)


def test_classify_salon_from_registry() -> None:
    registry = {"Салон Юг": {"channel_type": "mixed"}}
    bt, salon, channel, note = importer.classify_buyer("Салон Юг", registry)
    assert bt == "salon"
    assert salon == "Салон Юг"
    assert channel == "mixed"
    assert note is None


def test_unknown_buyer_not_classified_as_ip_or_salon() -> None:
    registry: dict = {}
    bt, salon, channel, note = importer.classify_buyer("Покупатель без справочника", registry)
    assert bt == "unknown"
    assert salon is None
    assert channel is None
    assert note == importer.ATTRIBUTION_NOTE
    assert importer.unknown_buyers_in_slice(
        [{"buyer": "Покупатель без справочника", "qty": 1.0, "amount": 1.0}],
        registry,
    ) == ["Покупатель без справочника"]


def test_legal_counterparty_is_ip_not_salon() -> None:
    bt, salon, channel, _ = importer.classify_buyer("ИП Псевдоним", {})
    assert bt == "ip"
    assert salon is None
    assert channel == "b2b"


def test_new_salon_in_registry_reclassifies_without_code_change(sample_db: Path) -> None:
    con = sqlite3.connect(sample_db)
    importer.ensure_schema(con)
    con.execute(
        "INSERT INTO salons (name, channel_type, is_active) VALUES (?,?,1)",
        ("Салон Юг", "online"),
    )
    con.commit()
    reg = importer.load_salon_registry(con.cursor())
    bt, salon, channel, note = importer.classify_buyer("Салон Юг", reg)
    con.close()
    assert bt == "salon"
    assert salon == "Салон Юг"
    assert channel == "online"
    assert note is None


def test_importer_source_has_no_forbidden_real_names() -> None:
    source = (importer.__file__)
    text = Path(source).read_text(encoding="utf-8")
    lower = text.lower()
    for fragment in _FORBIDDEN_IN_IMPORTER_SOURCE:
        assert fragment.lower() not in lower, f"forbidden fragment in importer.py: {fragment!r}"