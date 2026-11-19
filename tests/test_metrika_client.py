from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metrika_tools.client import (  # noqa: E402
    COSTS_MARGIN_DAY_METRICS,
    DayStats,
    MetrikaClient,
    MetrikaHTTPError,
    MetrikaTokenError,
    parse_totals_row,
)
from metrika_tools.cache import DiskCache as DiskCacheCls  # noqa: E402


class FakeHttpClient:
    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, *, headers: dict[str, str], timeout: float):
        self.calls.append((method, url))
        if not self.responses:
            raise RuntimeError("no more fake responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok(body: dict) -> object:
    from metrika_tools.client import HttpResponse

    return HttpResponse(status=200, body=json.dumps(body).encode(), headers={})


def _http_error(code: int, body: str = "err", headers: dict[str, str] | None = None) -> MetrikaHTTPError:
    return MetrikaHTTPError(code, body, headers or {})


class ParseTotalsTests(unittest.TestCase):
    def test_flat_totals(self) -> None:
        self.assertEqual(parse_totals_row([10, 20, 30]), [10.0, 20.0, 30.0])

    def test_nested_totals(self) -> None:
        self.assertEqual(parse_totals_row([[10], [20], [30]]), [10.0, 20.0, 30.0])


class RetryTests(unittest.TestCase):
    def test_retries_429_with_exponential_backoff(self) -> None:
        sleeps: list[float] = []
        body = {"totals": [1, 2, 3]}
        http = FakeHttpClient(
            [
                _http_error(429, "quota"),
                _http_error(429, "quota"),
                _ok(body),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskCacheCls(Path(tmp))
            client = MetrikaClient(
                token="fake-token",
                http_client=http,
                cache=cache,
                sleep_fn=sleeps.append,
                max_retries=5,
                retry_base_delay=2.0,
            )
            stats = client.fetch_day_stats(1, date(2026, 1, 1), metrics=COSTS_MARGIN_DAY_METRICS)
        self.assertEqual(stats.visits, 1.0)
        self.assertEqual(len(http.calls), 3)
        self.assertEqual(sleeps, [2.0, 4.0])

    def test_503_uses_retry_after(self) -> None:
        sleeps: list[float] = []
        http = FakeHttpClient(
            [
                _http_error(503, "busy", {"Retry-After": "7"}),
                _ok({"totals": [5, 6, 7]}),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            client = MetrikaClient(
                token="fake-token",
                http_client=http,
                cache=DiskCacheCls(Path(tmp)),
                sleep_fn=sleeps.append,
            )
            client.fetch_day_stats(2, date(2026, 1, 2), metrics=COSTS_MARGIN_DAY_METRICS)
        self.assertEqual(sleeps, [7.0])

    def test_401_not_retried(self) -> None:
        http = FakeHttpClient([_http_error(401, "nope")])
        with tempfile.TemporaryDirectory() as tmp:
            client = MetrikaClient(
                token="fake-token",
                http_client=http,
                cache=DiskCacheCls(Path(tmp)),
                sleep_fn=lambda _: None,
            )
            with self.assertRaises(MetrikaHTTPError) as ctx:
                client.fetch_day_stats(3, date(2026, 1, 3), metrics=COSTS_MARGIN_DAY_METRICS)
            self.assertEqual(ctx.exception.code, 401)
        self.assertEqual(len(http.calls), 1)

    def test_404_not_retried(self) -> None:
        http = FakeHttpClient([_http_error(404, "missing")])
        with tempfile.TemporaryDirectory() as tmp:
            client = MetrikaClient(
                token="fake-token",
                http_client=http,
                cache=DiskCacheCls(Path(tmp)),
            )
            with self.assertRaises(MetrikaHTTPError) as ctx:
                client.fetch_day_stats(4, date(2026, 1, 4), metrics=COSTS_MARGIN_DAY_METRICS)
            self.assertEqual(ctx.exception.code, 404)


class CacheTests(unittest.TestCase):
    def test_second_call_uses_cache(self) -> None:
        http = FakeHttpClient([_ok({"totals": [100, 200, 300]})])
        with tempfile.TemporaryDirectory() as tmp:
            client = MetrikaClient(
                token="fake-token",
                http_client=http,
                cache=DiskCacheCls(Path(tmp)),
            )
            day = date(2026, 2, 1)
            client.fetch_day_stats(10, day, metrics=COSTS_MARGIN_DAY_METRICS)
            client.fetch_day_stats(10, day, metrics=COSTS_MARGIN_DAY_METRICS)
        self.assertEqual(len(http.calls), 1)

    def test_expired_cache_refetches(self) -> None:
        http = FakeHttpClient(
            [
                _ok({"totals": [1, 1, 1]}),
                _ok({"totals": [2, 2, 2]}),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskCacheCls(Path(tmp), ttl_seconds=0.01)
            client = MetrikaClient(
                token="fake-token",
                http_client=http,
                cache=cache,
            )
            day = date(2026, 2, 2)
            first = client.fetch_day_stats(11, day, metrics=COSTS_MARGIN_DAY_METRICS)
            time.sleep(0.02)
            second = client.fetch_day_stats(11, day, metrics=COSTS_MARGIN_DAY_METRICS)
        self.assertEqual(first.visits, 1.0)
        self.assertEqual(second.visits, 2.0)
        self.assertEqual(len(http.calls), 2)

    def test_corrupt_cache_ignored(self) -> None:
        http = FakeHttpClient([_ok({"totals": [9, 8, 7]})])
        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp)
            cache = DiskCacheCls(cache_root)
            day = "2026-02-03"
            metrics_key = ",".join(COSTS_MARGIN_DAY_METRICS)
            path = cache._path_for_key(12, day, metrics_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not-json", encoding="utf-8")
            client = MetrikaClient(
                token="fake-token",
                http_client=http,
                cache=cache,
            )
            stats = client.fetch_day_stats(12, date(2026, 2, 3), metrics=COSTS_MARGIN_DAY_METRICS)
        self.assertEqual(stats.pageviews, 8.0)
        self.assertEqual(len(http.calls), 1)


class PaginationTests(unittest.TestCase):
    def test_fetch_dimension_fetches_all_pages(self) -> None:
        page1 = {
            "data": [{"dimensions": [{"name": "organic"}], "metrics": [10]}],
            "total_rows": 2,
        }
        page2 = {
            "data": [{"dimensions": [{"name": "direct"}], "metrics": [5]}],
            "total_rows": 2,
        }
        http = FakeHttpClient([_ok(page1), _ok(page2)])
        with tempfile.TemporaryDirectory() as tmp:
            client = MetrikaClient(
                token="fake-token",
                http_client=http,
                cache=DiskCacheCls(Path(tmp)),
            )
            rows = client.fetch_dimension(
                99,
                "ym:s:lastTrafficSource",
                date(2026, 3, 1),
                date(2026, 3, 1),
                page_limit=1,
            )
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(http.calls), 2)


class TokenTests(unittest.TestCase):
    def test_missing_token_raises_before_network(self) -> None:
        http = FakeHttpClient([_ok({"totals": [0, 0, 0]})])
        with tempfile.TemporaryDirectory() as tmp:
            client = MetrikaClient(
                token=None,
                http_client=http,
                cache=DiskCacheCls(Path(tmp)),
                require_token=True,
            )
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(MetrikaTokenError):
                    client.fetch_day_stats(1, date(2026, 1, 1), metrics=COSTS_MARGIN_DAY_METRICS)
        self.assertEqual(len(http.calls), 0)


class FetchRangeTests(unittest.TestCase):
    def test_fetch_range_one_request_per_day(self) -> None:
        http = FakeHttpClient(
            [
                _ok({"totals": [1, 2, 3]}),
                _ok({"totals": [4, 5, 6]}),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            client = MetrikaClient(
                token="fake-token",
                http_client=http,
                cache=DiskCacheCls(Path(tmp)),
            )
            series = client.fetch_range(
                7,
                date(2026, 4, 1),
                date(2026, 4, 2),
                metrics=COSTS_MARGIN_DAY_METRICS,
            )
        self.assertEqual(len(series), 2)
        self.assertEqual(series[0][1].visits, 1.0)
        self.assertEqual(series[1][1].visits, 4.0)
        self.assertEqual(len(http.calls), 2)


class DayStatsTests(unittest.TestCase):
    def test_from_totals_legacy(self) -> None:
        stats = DayStats.from_totals([1.5, 2.5, 90.0])
        self.assertEqual(stats.avg_duration_seconds, 90.0)


if __name__ == "__main__":
    unittest.main()