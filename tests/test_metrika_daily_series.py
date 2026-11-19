from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import metrika_daily_series as mds  # noqa: E402


def _payload(*values: object, nested: bool = False) -> dict:
    row = list(values)
    totals = [row] if nested else row
    return {"totals": totals}


class FakeClient:
    def __init__(self, responses: dict[tuple[int, date], dict | None | Exception]) -> None:
        self._responses = responses
        self.calls: list[tuple[int, date]] = []

    def fetch_day(self, counter_id: int, day: date) -> dict | None:
        self.calls.append((counter_id, day))
        item = self._responses.get((counter_id, day))
        if isinstance(item, Exception):
            raise item
        return item


class BuildDailySeriesTests(unittest.TestCase):
    def test_one_request_per_day_and_values(self) -> None:
        d1 = date(2026, 3, 1)
        d2 = date(2026, 3, 3)
        counter = 63403
        client = FakeClient(
            {
                (counter, d1): _payload(100, 200, 80, 25.0, 90),
                (counter, date(2026, 3, 2)): _payload(110, 210, 85, 24.0, 95),
                (counter, d2): _payload(120, 220, 90, 23.0, 100),
            }
        )
        series = mds.build_daily_series(client, counter, d1, d2, pace_seconds=0)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(client.calls[0], (counter, d1))
        self.assertEqual(client.calls[-1], (counter, d2))
        self.assertEqual(series[d1].visits, 100)
        self.assertEqual(series[date(2026, 3, 2)].pageviews, 210)
        self.assertEqual(series[d2].users, 90)

    def test_unavailable_counter_yields_gaps_not_exception(self) -> None:
        d1 = date(2026, 3, 1)
        d2 = date(2026, 3, 2)
        client = FakeClient({(999, d1): RuntimeError("network down"), (999, d2): None})
        series = mds.build_daily_series(client, 999, d1, d2, pace_seconds=0)
        self.assertIsNone(series[d1])
        self.assertIsNone(series[d2])
        self.assertTrue(mds.is_counter_unavailable(series))

    def test_single_gap_does_not_break_series(self) -> None:
        d1 = date(2026, 3, 1)
        d2 = date(2026, 3, 3)
        counter = 1
        client = FakeClient(
            {
                (counter, d1): _payload(10, 10, 10, 1.0, 1.0),
                (counter, date(2026, 3, 2)): None,
                (counter, d2): _payload(12, 12, 12, 1.0, 1.0),
            }
        )
        series = mds.build_daily_series(client, counter, d1, d2, pace_seconds=0)
        self.assertIsNotNone(series[d1])
        self.assertIsNone(series[date(2026, 3, 2)])
        self.assertIsNotNone(series[d2])
        self.assertFalse(mds.is_counter_unavailable(series))


class DynamicsTests(unittest.TestCase):
    def test_growth_and_decline_format(self) -> None:
        self.assertEqual(mds.format_change_pct(112.5, 100), "+12.5%")
        self.assertEqual(mds.format_change_pct(96.9, 100), "-3.1%")

    def test_zero_previous_is_new(self) -> None:
        self.assertEqual(mds.format_change_pct(50, 0), mds.NEW_PERIOD_LABEL)
        self.assertEqual(mds.format_change_pct(0, 0), "+0.0%")


class WeightedAverageTests(unittest.TestCase):
    def test_weighted_differs_from_simple(self) -> None:
        pairs = [(50.0, 10.0), (10.0, 1000.0)]
        weighted = mds.weighted_average(pairs)
        simple = mds.simple_average([50.0, 10.0])
        self.assertAlmostEqual(weighted, (50 * 10 + 10 * 1000) / 1010)
        self.assertAlmostEqual(simple, 30.0)
        self.assertNotAlmostEqual(weighted, simple)


class BrokenPayloadTests(unittest.TestCase):
    def test_nested_totals(self) -> None:
        agg = mds.parse_day_aggregates(_payload(1, 2, 3, 4, 5, nested=True))
        self.assertIsNotNone(agg)
        assert agg is not None
        self.assertEqual(agg.visits, 1)
        self.assertEqual(agg.pageviews, 2)

    def test_none_and_string_coerce_to_zero(self) -> None:
        agg = mds.parse_day_aggregates(_payload(1, None, "bad", "12.5", 3))
        self.assertIsNotNone(agg)
        assert agg is not None
        self.assertEqual(agg.pageviews, 0)
        self.assertEqual(agg.users, 0)
        self.assertEqual(agg.bounce_rate, 12.5)

    def test_missing_totals_is_gap(self) -> None:
        self.assertIsNone(mds.parse_day_aggregates({}))
        self.assertIsNone(mds.parse_day_aggregates({"totals": []}))


class RegistryAndSummaryTests(unittest.TestCase):
    def test_registry_has_sites_and_landings(self) -> None:
        registry = mds.load_counter_registry()
        sites, landings = mds.partition_by_kind(registry)
        self.assertEqual(len(registry), 10)
        self.assertEqual(len(sites), 2)
        self.assertEqual(len(landings), 8)
        site_ids = {row.counter_id for row in sites}
        self.assertEqual(site_ids, {63403, 94834593})

    def test_summaries_split_by_kind(self) -> None:
        registry = mds.load_counter_registry()
        day = date(2026, 3, 1)
        series_by_counter = {
            63403: {day: mds.DayAggregates(100, 100, 100, 40.0, 60.0)},
            94834593: {day: mds.DayAggregates(50, 50, 50, 20.0, 30.0)},
            110992850: {day: mds.DayAggregates(200, 200, 200, 10.0, 10.0)},
        }
        sites, landings = mds.summarize_groups(registry, series_by_counter)
        self.assertEqual(sites.visits, 150)
        self.assertEqual(landings.visits, 200)
        self.assertEqual(sites.counters_included, 2)
        self.assertEqual(landings.counters_included, 1)

    def test_counter_dynamics_partial_series(self) -> None:
        registry = [mds.CounterRecord(1, "Demo", "site")]
        current = {date(2026, 3, 2): mds.DayAggregates(120, 0, 0, 0, 0)}
        previous = {date(2026, 3, 1): mds.DayAggregates(100, 0, 0, 0, 0)}
        rows = mds.build_counter_dynamics(registry, {1: current}, {1: previous})
        self.assertEqual(rows[0].visits_change, "+20.0%")


if __name__ == "__main__":
    unittest.main()