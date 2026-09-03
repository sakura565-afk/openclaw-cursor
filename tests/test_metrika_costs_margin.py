from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import metrika_costs_margin as mcm  # noqa: E402


class NormalizeSkuTests(unittest.TestCase):
    def test_strips_quotes_and_uppercases(self) -> None:
        self.assertEqual(mcm.normalize_sku('"sofa-arizona"'), "SOFA-ARIZONA")

    def test_normalizes_cyrillic_lookalikes(self) -> None:
        # "СОФА" mixes Cyrillic С/О/Ф/А with a Latin-lookalike table; letters
        # present in the translation table collapse to their Latin form.
        self.assertEqual(mcm.normalize_sku("СОФА"), mcm.normalize_sku("COФA"))

    def test_drops_standard_suffix(self) -> None:
        self.assertEqual(mcm.normalize_sku("SOFA-ARIZONA-STANDART"), "SOFA-ARIZONA")

    def test_empty_input(self) -> None:
        self.assertEqual(mcm.normalize_sku(None), "")
        self.assertEqual(mcm.normalize_sku(""), "")


class ArticleKeyTests(unittest.TestCase):
    def test_first_token_before_dash(self) -> None:
        self.assertEqual(mcm.article_key("AM-1234-STANDART"), "AM")

    def test_no_dash(self) -> None:
        self.assertEqual(mcm.article_key("SOFA123"), "SOFA123")


class MarginTests(unittest.TestCase):
    def test_compute_margin_basic(self) -> None:
        # revenue ex-vat = 12000/1.2 = 10000; margin = 10000-6000 = 4000 -> 40%
        result = mcm.compute_margin(12000.0, 6000.0)
        self.assertIsNotNone(result)
        margin_rub, margin_pct = result
        self.assertAlmostEqual(margin_rub, 4000.0)
        self.assertAlmostEqual(margin_pct, 40.0)

    def test_compute_margin_zero_revenue(self) -> None:
        self.assertIsNone(mcm.compute_margin(0.0, 100.0))

    def test_item_cost_with_vat_from_cost(self) -> None:
        item = {"cost": 1000.0}
        self.assertAlmostEqual(mcm.item_cost_with_vat(item, {}), 1200.0)

    def test_item_cost_with_vat_prefers_fabric(self) -> None:
        item = {
            "cost_with_vat": 1200.0,
            "cost_by_fabric_with_vat": {"velvet": 1500.0},
        }
        sale = {"fabric": "velvet"}
        self.assertAlmostEqual(mcm.item_cost_with_vat(item, sale), 1500.0)


class CrossCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = [
            {
                "sku": "SOFA-ARIZONA-STANDART",
                "name": "Sofa Arizona",
                "category": "Мягкая мебель",
                "cost": 5000.0,
                "cost_with_vat": 6000.0,
            },
            {
                "sku": "BED-1200",
                "name": "Bed 1200",
                "category": "Кровати&матрасы",
                "cost": 3000.0,
                "cost_with_vat": 3600.0,
            },
        ]
        self.sales = [
            {"sku": "SOFA-ARIZONA", "category": "Мягкая мебель", "total": 12000.0},
            {"sku": "BED-1200", "category": "Кровати&матрасы", "total": 6000.0},
            {"sku": "UNKNOWN-SKU", "category": "Декор", "total": 1000.0},
        ]

    def test_matches_normalized_sku_dropping_suffix(self) -> None:
        matched, total = mcm.cross_check(self.sales, self.items)
        self.assertEqual(total, 3)
        matched_skus = {m.sale["sku"] for m in matched}
        self.assertIn("SOFA-ARIZONA", matched_skus)
        self.assertIn("BED-1200", matched_skus)
        self.assertEqual(len(matched), 2)

    def test_margin_values(self) -> None:
        matched, _ = mcm.cross_check(self.sales, self.items)
        by_sku = {m.sale["sku"]: m for m in matched}
        self.assertAlmostEqual(by_sku["SOFA-ARIZONA"].margin_pct, 40.0)
        self.assertAlmostEqual(by_sku["BED-1200"].margin_pct, 28.0, places=1)

    def test_category_table_sorted_desc_by_margin(self) -> None:
        matched, _ = mcm.cross_check(self.sales, self.items)
        lines = mcm.build_category_table(matched)
        sofa_idx = next(i for i, line in enumerate(lines) if "Мягкая мебель" in line)
        bed_idx = next(i for i, line in enumerate(lines) if "Кровати&матрасы" in line)
        self.assertLess(sofa_idx, bed_idx)  # higher margin category listed first


class LoadersGracefulTests(unittest.TestCase):
    def test_load_costs_db_missing_dir(self) -> None:
        items, path, error = mcm.load_costs_db(date(2026, 9, 2), Path("/nonexistent/costs/dir"))
        self.assertEqual(items, [])
        self.assertIsNone(path)
        self.assertIsNotNone(error)

    def test_load_sales_missing_file(self) -> None:
        records, error = mcm.load_sales(Path("/nonexistent/sales.json"))
        self.assertEqual(records, [])
        self.assertIsNotNone(error)

    def test_load_costs_db_reads_real_file(self, tmp_path: Path | None = None) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            costs_dir = Path(tmpdir)
            payload = [{"sku": "A", "cost": 100.0, "cost_with_vat": 120.0, "category": "Декор"}]
            (costs_dir / "costs_2026-09-01.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            items, path, error = mcm.load_costs_db(date(2026, 9, 1), costs_dir)
            self.assertIsNone(error)
            self.assertEqual(len(items), 1)
            self.assertEqual(path.name, "costs_2026-09-01.json")


class ReportBuildTests(unittest.TestCase):
    def test_build_report_runs_without_data(self) -> None:
        report = mcm.build_report(
            date(2026, 9, 2),
            [(63403, "amadey.ru", None, "no token")],
            [],
            "sales file not found",
            [],
            None,
            "costs directory not found",
        )
        self.assertIn("## Yandex Metrika + Costs — 2026-09-02", report)
        self.assertIn("amadey.ru", report)
        self.assertIn("No sales data available", report)
        self.assertIn("No cost data available", report)


class ResolveCategoryTests(unittest.TestCase):
    """Override-based category resolution (mirrors -> Classic furniture)."""

    def test_mirror_in_name_reclassified_to_classic(self) -> None:
        cost = {"name": "Зеркало напольное MF-102", "category": "Декор"}
        sale = {"sku": "Зеркало напольное 'Амадей MF-102'", "category": "Декор"}
        self.assertEqual(mcm.resolve_category(cost, sale), "Классическая мебель")

    def test_mirror_in_sku_only_reclassified_to_classic(self) -> None:
        # cost_item.name may be stale (e.g. "Стол садовый Rio белый") while the
        # sale.sku is the authoritative mirror reference; the override must
        # still hit. Regression case from 2026-09-03.
        cost = {"name": "Стол садовый Rio белый", "category": "Декор"}
        sale = {"sku": "Зеркало напольное 'Амадей MF-102'", "category": "Декор"}
        self.assertEqual(mcm.resolve_category(cost, sale), "Классическая мебель")

    def test_non_mirror_decor_kept_as_decor(self) -> None:
        cost = {"name": "Подставка для обуви SS 102", "category": "Декор"}
        sale = {"sku": "Подставка 01 арт.", "category": "Декор"}
        self.assertEqual(mcm.resolve_category(cost, sale), "Декор")

    def test_other_categories_unaffected(self) -> None:
        cost = {"name": "Диван Селва", "category": "Мягкая мебель"}
        sale = {"sku": "Диван Селва", "category": "Мягкая мебель"}
        self.assertEqual(mcm.resolve_category(cost, sale), "Мягкая мебель")

    def test_missing_category_falls_back_to_unknown(self) -> None:
        cost = {"name": "Item without category"}
        sale = {"sku": "X"}
        self.assertEqual(mcm.resolve_category(cost, sale), "(unknown)")

    def test_override_does_not_match_substring_of_unrelated_word(self) -> None:
        # Guard against future Cyrillic stem collisions: "Зер" must NOT trigger.
        cost = {"name": "Зернодробилка", "category": "Декор"}
        sale = {"sku": "Зернодробилка бытовая", "category": "Декор"}
        self.assertEqual(mcm.resolve_category(cost, sale), "Декор")


if __name__ == "__main__":
    unittest.main()
