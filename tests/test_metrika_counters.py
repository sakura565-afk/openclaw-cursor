from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metrika_tools import counters as mc  # noqa: E402
from metrika_tools import sources as ms  # noqa: E402

EXPECTED_IDS = frozenset(
    {
        63403,
        94834593,
        110795180,
        112584784,
        114091411,
        110992850,
        111303025,
        113522944,
        111529130,
        111602665,
    }
)


class LoadCountersTests(unittest.TestCase):
    def test_default_inventory_has_ten_counters(self) -> None:
        counters = mc.load_counters()
        self.assertEqual(len(counters), 10)
        self.assertEqual({c.id for c in counters}, EXPECTED_IDS)

    def test_ids_are_unique(self) -> None:
        counters = mc.load_counters()
        self.assertEqual(len({c.id for c in counters}), len(counters))


class ValidateCountersTests(unittest.TestCase):
    def _write_inventory(self, payload: dict) -> Path:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(payload, tmp, ensure_ascii=False)
        tmp.close()
        return Path(tmp.name)

    def test_duplicate_id_raises(self) -> None:
        path = self._write_inventory(
            {
                "counters": [
                    {"id": 1, "name": "A", "type": "main", "domain": "a.ru"},
                    {"id": 1, "name": "B", "type": "landing", "domain": "b.ru"},
                ]
            }
        )
        self.addCleanup(path.unlink, missing_ok=True)
        with self.assertRaises(mc.CounterRegistryError) as ctx:
            mc.load_counters(path)
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_missing_field_raises(self) -> None:
        path = self._write_inventory({"counters": [{"id": 1, "name": "A", "type": "main"}]})
        self.addCleanup(path.unlink, missing_ok=True)
        with self.assertRaises(mc.CounterRegistryError) as ctx:
            mc.load_counters(path)
        self.assertIn("domain", str(ctx.exception))

    def test_invalid_id_type_raises(self) -> None:
        path = self._write_inventory(
            {"counters": [{"id": "63403", "name": "A", "type": "main", "domain": "a.ru"}]}
        )
        self.addCleanup(path.unlink, missing_ok=True)
        with self.assertRaises(mc.CounterRegistryError):
            mc.load_counters(path)

    def test_non_positive_id_raises(self) -> None:
        path = self._write_inventory(
            {"counters": [{"id": 0, "name": "A", "type": "main", "domain": "a.ru"}]}
        )
        self.addCleanup(path.unlink, missing_ok=True)
        with self.assertRaises(mc.CounterRegistryError):
            mc.load_counters(path)


class ReportOrderTests(unittest.TestCase):
    def test_main_counters_first(self) -> None:
        ordered = mc.counters_report_order(mc.load_counters())
        types = [c.type for c in ordered]
        first_landing = types.index("landing")
        self.assertTrue(all(t == "main" for t in types[:first_landing]))
        self.assertEqual(len([t for t in types if t == "main"]), 2)
        self.assertEqual(ordered[0].id, 63403)
        self.assertEqual(ordered[1].id, 94834593)


class ParseSourceTests(unittest.TestCase):
    def test_direct(self) -> None:
        self.assertEqual(ms.parse_source("ym:s:direct"), "Прямые заходы")
        self.assertEqual(ms.parse_source("  YM:S:DIRECT  "), "Прямые заходы")

    def test_organic(self) -> None:
        self.assertEqual(ms.parse_source("ym:s:organic"), "Органический поиск")

    def test_utm_pair(self) -> None:
        self.assertEqual(ms.parse_source("ym:s:yandex:brand"), "yandex / brand")
        self.assertEqual(ms.parse_source("ym:s:vk:  летняя  "), "vk / летняя")

    def test_search_engine_bracket(self) -> None:
        self.assertEqual(ms.parse_source("ym:s:searchEngine<Google>"), "Google")

    def test_domain_passthrough(self) -> None:
        self.assertEqual(ms.parse_source("ym:s:partner.example.com"), "partner.example.com")

    def test_empty_and_none(self) -> None:
        self.assertEqual(ms.parse_source(""), ms.UNDEFINED_SOURCE_LABEL)
        self.assertEqual(ms.parse_source("   "), ms.UNDEFINED_SOURCE_LABEL)
        self.assertEqual(ms.parse_source(None), ms.UNDEFINED_SOURCE_LABEL)

    def test_cyrillic_utm_not_broken(self) -> None:
        label = ms.parse_source("ym:s:яндекс:мебель_осень")
        self.assertEqual(label, "яндекс / мебель_осень")


class SourceDimensionsTests(unittest.TestCase):
    def test_registry_contains_required_dimensions(self) -> None:
        ids = {d.dimension for d in ms.SOURCE_DIMENSIONS}
        expected = {
            "ym:s:source",
            "ym:s:UTMSource",
            "ym:s:UTMCampaign",
            "ym:s:referrer",
            "ym:s:searchEngine",
            "ym:s:direct",
            "ym:s:organic",
            "ym:s:social",
            "ym:s:email",
            "ym:s:ads",
            "ym:s:campaign",
        }
        self.assertEqual(ids, expected)


if __name__ == "__main__":
    unittest.main()