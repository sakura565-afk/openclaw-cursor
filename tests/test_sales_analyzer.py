from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

from furniture_sales_database.sales_tools.analyzer import (
    _SUPPLIER_GROUP,
    analyze,
    group_of,
    is_supplier_sale,
    period_days,
    render_json,
)
from furniture_sales_database.sales_tools.analyzer import _ensure_utf8_stdout

FIXTURE_DB = (
    Path(__file__).resolve().parents[1]
    / "furniture_sales_database/sales_tools/fixtures/sales_sample.db"
)


@pytest.fixture(scope="module")
def sample_db() -> str:
    assert FIXTURE_DB.is_file(), "fixture sales_sample.db missing"
    return FIXTURE_DB.as_posix()


def test_analyze_groups_sum_matches_slice(sample_db: str) -> None:
    report = analyze(sample_db, "2026-11")
    groups_sum = sum(g["amount"] for g in report["groups"])
    assert abs(groups_sum - report["slice"]["amount"]) < 0.01


def test_coverage_ok_and_no_unassigned(sample_db: str) -> None:
    report = analyze(sample_db, "2026-11")
    assert report["coverage"]["sum_ok"] is True
    assert report["coverage"]["unassigned"] == []


def test_dynamics_per_day_uses_period_split(sample_db: str) -> None:
    report = analyze(sample_db, "2026-11", compare_month="2026-10")
    assert report["dynamics"], "expected dynamics vs 2026-10"
    days = period_days(report["slice"]["period"])
    assert days == 18
    by_group = {d["group"]: d for d in report["dynamics"]}
    amt = next(g["amount"] for g in report["groups"] if g["name"] == "Стулья")
    assert abs(by_group["Стулья"]["per_day"] - amt / days) < 0.01
    assert by_group["Стулья"]["delta_pct"] is not None


def test_supplier_group_by_buyer_type_not_parent_name() -> None:
    """Поставщик — по buyer_type, а не по совпадению parent с реальным именем."""
    unrelated_parent = "Классика"
    assert group_of(
        "Стул демо, шт",
        unrelated_parent,
        buyer_type="supplier",
    ) == _SUPPLIER_GROUP
    assert (
        group_of("Стул демо, шт", unrelated_parent, buyer_type="salon")
        == "Стулья"
    )
    assert is_supplier_sale("supplier", None, frozenset()) is True
    assert is_supplier_sale("salon", "Поставщик Цветы", frozenset()) is True


def test_no_cyrillic_buyer_name_literals_in_analyzer_module() -> None:
    """Нет module-level dict/set с кириллическими ключами-именами контрагентов."""
    root = Path(__file__).resolve().parents[1]
    src = (root / "furniture_sales_database/sales_tools/analyzer.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(src)
    counterparty_pattern = re.compile(r"\bИП\b|ООО|«")
    offenders: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        val = node.value
        if not isinstance(val, (ast.Dict, ast.Set)):
            continue
        keys = val.keys if isinstance(val, ast.Dict) else val.elts
        for key in keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                text = key.value
                if " " in text and (
                    counterparty_pattern.search(text)
                    or (text != text.lower() and len(text) > 18)
                ):
                    offenders.append(text)
    assert offenders == []
    assert "SUPPLIER_PARENTS" not in src


def test_group_names_survive_cp1251_roundtrip() -> None:
    """Названия групп не должны схлопываться при cp1251 -> utf-8.

    Реальная поломка: под планировщиком Windows дочерний процесс печатал
    JSON в cp1251, родитель декодировал utf-8. «Стулья», «Кресла» и
    «Диваны» превращались в три ОДИНАКОВЫЕ строки из «�», из-за чего
    словарь динамики отдавал разным группам одно и то же значение
    (в отчёте у всех трёх было «41 025/дн +43%»), а читателю уходили
    нечитаемые названия.
    """
    groups = ["Стулья", "Кресла", "Диваны", "Кровати", "Столы"]
    broken = [g.encode("cp1251").decode("utf-8", errors="replace") for g in groups]
    assert len(set(broken)) < len(groups), (
        "фикстура перестала воспроизводить поломку — тест больше не проверяет"
    )

    # Анализатор обязан отдавать группы различимыми и без U+FFFD.
    report = analyze(FIXTURE_DB.as_posix(), "2026-11")
    names = [g["name"] for g in report["groups"]]
    assert names, "анализатор не вернул групп"
    assert len(set(names)) == len(names), "названия групп схлопнулись"
    for name in names:
        assert "\ufffd" not in name, f"искажённое название группы: {name!r}"


def test_ensure_utf8_stdout_is_callable_and_safe() -> None:
    """Хелпер переключает stdout на UTF-8 и не падает на потоках без reconfigure."""
    _ensure_utf8_stdout()  # не должно бросать исключение
    assert sys.stdout is not None


def _supplier_group_amount(report: dict) -> float:
    for g in report["groups"]:
        if g["name"] == _SUPPLIER_GROUP:
            return g["amount"]
    return 0.0


def test_november_supplier_group_zero(sample_db: str) -> None:
    report = analyze(sample_db, "2026-11")
    assert _supplier_group_amount(report) == 0.0


def test_october_supplier_group_matches_legacy_parent_rule(sample_db: str) -> None:
    """98 500 ₽ — результат по parent_category поставщиков в срезе октября."""
    report = analyze(sample_db, "2026-10", compare_month="2026-09")
    assert abs(_supplier_group_amount(report) - 98_500.0) < 0.01


def test_render_json_valid_and_sum_ok(sample_db: str) -> None:
    report = analyze(sample_db, "2026-11")
    raw = render_json(report)
    payload = json.loads(raw)
    assert payload["coverage"]["sum_ok"] is True
    groups_sum = sum(g["amount"] for g in payload["groups"])
    assert abs(groups_sum - payload["slice"]["amount"]) < 0.01