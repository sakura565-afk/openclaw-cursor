"""Tests for furniture_sales_database.sales_tools.importer (no network, tmp_path DB)."""

from __future__ import annotations

import ast
import re
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


NOV_ROW_COUNT = 112
NOV_QTY = 268.0
NOV_AMOUNT = 5_766_389.0

SALES_TOOLS_PKG = FIXTURE_DB.parent.parent
_REGISTRY_VAR_RE = re.compile(
    r"(KNOWN_|SUPPLIER_|_SALONS|_BUYERS|BUYER_REGISTRY)",
    re.IGNORECASE,
)
_INLINE_BUYER_LITERAL_RE = re.compile(
    r'(?:Салон\s+[А-Яа-яЁё«"\[]|ИП\s+[А-Яа-яЁё]|ООО\s+[«"\u00AB])'
)


def _sales_tools_py_sources() -> list[Path]:
    return sorted(SALES_TOOLS_PKG.glob("*.py"))


def _fixture_counterparty_names() -> set[str]:
    con = sqlite3.connect(FIXTURE_DB)
    names: set[str] = set()
    for (name,) in con.execute("SELECT DISTINCT name FROM buyers"):
        if name:
            names.add(name)
    for (buyer,) in con.execute("SELECT DISTINCT buyer FROM sales"):
        if buyer:
            names.add(buyer)
    con.close()
    return names


def _literal_keys_from_dict_node(node: ast.Dict) -> list[str]:
    keys: list[str] = []
    for k in node.keys:
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            keys.append(k.value)
    return keys


def _looks_like_buyer_name_registry(keys: list[str]) -> bool:
    if not keys or not all(isinstance(k, str) for k in keys):
        return False
    if len(keys) == 1:
        k = keys[0]
        return (" " in k and len(k) > 12) or any(m in k for m in ("Салон", "ИП ", "ООО"))
    markers = sum(
        1
        for k in keys
        if (" " in k and len(k) > 8)
        or k.startswith("Салон")
        or k.startswith("ИП")
        or "ООО" in k
        or "«" in k
    )
    return markers >= max(1, len(keys) // 2)


def _module_level_buyer_registries(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            var_name = target.id
            value = node.value
            keys: list[str] = []
            if isinstance(value, ast.Dict):
                keys = _literal_keys_from_dict_node(value)
            elif isinstance(value, ast.Set):
                keys = [
                    elt.value
                    for elt in value.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
            if not keys:
                continue
            if _REGISTRY_VAR_RE.search(var_name) or _looks_like_buyer_name_registry(keys):
                hits.append(f"{var_name} ({len(keys)} entries)")
    return hits


def _hardcoded_buyer_literals_in_source(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        s = node.value
        if _INLINE_BUYER_LITERAL_RE.search(s) and len(s) < 120:
            # Шаблоны юрформ без конкретного контрагента — ок.
            if s.startswith("Общество с ограниченной") or s.startswith("ИНДИВИДУАЛЬНЫЙ"):
                continue
            if "предприниматель" in s.lower() and " " not in s[30:]:
                continue
            hits.append(s[:60])
    return hits


# analyzer.py: SUPPLIER_PARENTS — известный долг (задача 2), здесь не правим.
_HYGIENE_SKIP_FILES = frozenset({"analyzer.py"})


def test_no_hardcoded_buyer_registry() -> None:
    """Пакет sales_tools: нет справочников покупателей в коде."""
    for path in _sales_tools_py_sources():
        if path.name in _HYGIENE_SKIP_FILES:
            continue
        registries = _module_level_buyer_registries(path)
        assert not registries, f"{path.name}: module-level buyer registry {registries}"

    fixture_names = _fixture_counterparty_names()
    for path in _sales_tools_py_sources():
        if path.name in _HYGIENE_SKIP_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        leaked = [n for n in fixture_names if n in text]
        assert not leaked, f"{path.name}: fixture counterparty names in source: {leaked[:3]}"

    for path in _sales_tools_py_sources():
        if path.name in _HYGIENE_SKIP_FILES:
            continue
        literals = _hardcoded_buyer_literals_in_source(path)
        assert not literals, f"{path.name}: hardcoded buyer literals {literals[:3]}"


def _fixture_november_salon_buyers() -> set[str]:
    con = sqlite3.connect(FIXTURE_DB)
    names = {
        r[0]
        for r in con.execute(
            "SELECT DISTINCT buyer FROM sales WHERE month=? AND buyer_type='salon'",
            (MONTH,),
        ).fetchall()
    }
    con.close()
    return names


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
    for salon in _fixture_november_salon_buyers():
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
    split_sum = 0.0
    for bt in ("ip", "salon", "unknown"):
        part = con.execute(
            "SELECT COALESCE(SUM(amount),0) FROM sales WHERE month=? AND buyer_type=?",
            (MONTH, bt),
        ).fetchone()[0]
        split_sum += float(part)
    con.close()

    assert row_cnt == NOV_ROW_COUNT
    assert float(qty) == NOV_QTY
    assert float(amt) == NOV_AMOUNT
    assert by_type.get("salon") == 100
    assert by_type.get("ip") == 12
    assert by_type.get("unknown", 0) == 0
    assert set(salon_buyers) == _fixture_november_salon_buyers()
    assert abs(split_sum - float(amt)) < 0.01


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


@pytest.mark.parametrize(
    "name",
    [
        'Общество с ограниченной ответственностью  "Прогресс"',
        "ИНДИВИДУАЛЬНЫЙ ПРЕДПРИНИМАТЕЛЬ ПСЕВДОНИМ АННА СЕРГЕЕВНА",
        'Акционерное общество "ХолдингТест"',
        'ЗАО "Ромашка"',
        "Город ИП Контрагент М.С.",
        'ООО «Псевдоним»',
    ],
)
def test_legal_name_forms_classify_as_ip(name: str) -> None:
    bt, salon, channel, _ = importer.classify_buyer(name, {})
    assert bt == "ip"
    assert salon is None
    assert channel == "b2b"


@pytest.mark.parametrize(
    "name",
    [
        "Торговый дом Альфа",
        "БрендБезЮрФормы",
        "ООО",
    ],
)
def test_non_legal_names_classify_as_unknown_without_registry(name: str) -> None:
    bt, _, _, note = importer.classify_buyer(name, {})
    assert bt == "unknown"
    assert note == importer.ATTRIBUTION_NOTE


def test_november_no_unknown_buyers_in_slice(sample_db: Path) -> None:
    con = sqlite3.connect(sample_db)
    importer.ensure_schema(con)
    reg = importer.load_salon_registry(con.cursor())
    _, leaves, _ = _load_month_payload(con, MONTH)
    buyers = {x["buyer"] for x in leaves}
    unknown = [b for b in buyers if importer.classify_buyer(b, reg)[0] == "unknown"]
    con.close()
    assert unknown == []


def test_buyer_type_totals_cover_slice_amount(sample_db: Path) -> None:
    con = sqlite3.connect(sample_db)
    parsed, leaves, source_file = _load_month_payload(con, MONTH)
    importer.apply_import(con, parsed, leaves, source_file)
    totals = importer.buyer_type_totals(con.cursor(), MONTH)
    slice_amt = con.execute(
        "SELECT SUM(amount) FROM sales WHERE month=?", (MONTH,)
    ).fetchone()[0]
    assert totals.get("unknown", (0, 0.0))[0] == 0
    assert sum(amt for _c, amt in totals.values()) == pytest.approx(float(slice_amt))
    con.close()


def test_non_legal_salon_name_unknown_without_registry() -> None:
    con = sqlite3.connect(FIXTURE_DB)
    sample_salon = con.execute(
        "SELECT buyer FROM sales WHERE month=? AND buyer_type='salon' LIMIT 1",
        (MONTH,),
    ).fetchone()[0]
    con.close()
    bt, _, _, _ = importer.classify_buyer(sample_salon, {})
    assert bt == "unknown"