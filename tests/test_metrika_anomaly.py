from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import metrika_anomaly as ma  # noqa: E402


def _series(
    name: str,
    *,
    counter_id: int | str = 1,
    kind: str = "site",
    start: date,
    visits: list[float],
) -> ma.CounterSeries:
    points = tuple(
        ma.DayPoint(day=start + timedelta(days=i), visits=v, visitors=v * 0.8)
        for i, v in enumerate(visits)
    )
    return ma.CounterSeries(counter_id=counter_id, name=name, kind=kind, points=points)


class BaselineTests(unittest.TestCase):
    def test_baseline_excludes_today(self) -> None:
        today = date(2026, 8, 22)
        values = {today - timedelta(days=i): float(100 + i) for i in range(1, 16)}
        values[today] = 9999.0
        samples = ma.baseline_samples(values, today, days=14)
        self.assertEqual(len(samples), 14)
        self.assertNotIn(9999.0, samples)
        self.assertAlmostEqual(ma.compute_baseline(samples), sum(samples) / 14)

    def test_z_score_positive_rise(self) -> None:
        samples = [100.0] * 13 + [110.0]
        z = ma.compute_z_score(250.0, samples)
        self.assertGreater(z, 0.0)


class AnomalyDetectionTests(unittest.TestCase):
    def test_site_drop_detected(self) -> None:
        start = date(2026, 8, 1)
        visits = [100.0] * 15 + [50.0]
        series = _series("amadey.ru", counter_id=94834593, start=start, visits=visits)
        today = start + timedelta(days=15)
        anomaly = ma.detect_counter_anomaly(series, today)
        self.assertIsNotNone(anomaly)
        assert anomaly is not None
        self.assertEqual(anomaly.anomaly_type, "drop")
        self.assertLess(anomaly.delta_pct, -35.0)

    def test_site_rise_detected(self) -> None:
        start = date(2026, 8, 1)
        visits = [100.0] * 15 + [250.0]
        series = _series("divaninfo.ru", counter_id=63403, start=start, visits=visits)
        today = start + timedelta(days=15)
        anomaly = ma.detect_counter_anomaly(series, today)
        self.assertIsNotNone(anomaly)
        assert anomaly is not None
        self.assertEqual(anomaly.anomaly_type, "rise")
        self.assertGreaterEqual(anomaly.delta_pct, 100.0)

    def test_landing_uses_stricter_drop(self) -> None:
        start = date(2026, 8, 1)
        visits = [100.0] * 15 + [60.0]
        series = _series("landing/promo", kind="landing", start=start, visits=visits)
        today = start + timedelta(days=15)
        self.assertIsNone(ma.detect_counter_anomaly(series, today))

    def test_landing_drop_at_50_percent(self) -> None:
        start = date(2026, 8, 1)
        visits = [100.0] * 15 + [49.0]
        series = _series("landing/promo", kind="landing", start=start, visits=visits)
        today = start + timedelta(days=15)
        anomaly = ma.detect_counter_anomaly(series, today)
        self.assertIsNotNone(anomaly)
        assert anomaly is not None
        self.assertEqual(anomaly.anomaly_type, "drop")

    def test_no_anomaly_within_threshold(self) -> None:
        start = date(2026, 8, 1)
        visits = [100.0] * 15 + [90.0]
        series = _series("amadey.ru", start=start, visits=visits)
        today = start + timedelta(days=15)
        self.assertIsNone(ma.detect_counter_anomaly(series, today))


class AlertDedupTests(unittest.TestCase):
    def test_active_keys_skip_repeat_alert(self) -> None:
        history = [
            {
                "date": "2026-08-20",
                "key": "94834593:visits:drop",
                "alerted": True,
                "recovered": False,
            }
        ]
        active = ma.active_alert_keys(history)
        self.assertIn("94834593:visits:drop", active)

    def test_recovery_clears_active_key(self) -> None:
        history = [
            {"date": "2026-08-20", "key": "94834593:visits:drop", "alerted": True},
            {"date": "2026-08-22", "key": "94834593:visits:drop", "recovered": True},
        ]
        active = ma.active_alert_keys(history)
        self.assertNotIn("94834593:visits:drop", active)

    def test_first_run_does_not_alert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = ma.cache_dir(root)
            cache.mkdir(parents=True)
            start = date(2026, 8, 1)
            payload = {
                "counter_id": 94834593,
                "name": "amadey.ru",
                "kind": "site",
                "daily": [
                    {
                        "date": (start + timedelta(days=i)).isoformat(),
                        "visits": 100 if i < 15 else 40,
                        "visitors": 80,
                    }
                    for i in range(16)
                ],
            }
            (cache / "94834593.json").write_text(json.dumps(payload), encoding="utf-8")

            alert = mock.Mock(return_value={"ok": True})
            with mock.patch.object(ma, "send_telegram_alert", alert):
                report = ma.run_detector(
                    root=root,
                    today=start + timedelta(days=15),
                    send_alert=True,
                    update_history=True,
                )
            self.assertTrue(report["first_run"])
            self.assertEqual(len(report["new_alerts"]), 0)
            alert.assert_not_called()
            self.assertTrue(ma.anomalies_path(root).is_file())


class JsonlAppendTests(unittest.TestCase):
    def test_append_jsonl_creates_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "metrika_anomalies.jsonl"
            rec = {"date": "2026-08-22", "key": "1:visits:drop", "alerted": True}
            ma.append_jsonl(path, [rec])
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["key"], "1:visits:drop")

    def test_second_run_alerts_new_anomaly_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = ma.cache_dir(root)
            cache.mkdir(parents=True)
            start = date(2026, 8, 1)
            today = start + timedelta(days=15)
            payload = {
                "counter_id": 94834593,
                "name": "amadey.ru",
                "kind": "site",
                "daily": [
                    {
                        "date": (start + timedelta(days=i)).isoformat(),
                        "visits": 100 if i < 15 else 40,
                        "visitors": 80,
                    }
                    for i in range(16)
                ],
            }
            (cache / "94834593.json").write_text(json.dumps(payload), encoding="utf-8")

            ma.run_detector(root=root, today=today, send_alert=False, update_history=True)

            alert = mock.Mock(return_value={"ok": True})
            with mock.patch.object(ma, "send_telegram_alert", alert):
                report = ma.run_detector(
                    root=root,
                    today=today + timedelta(days=1),
                    send_alert=True,
                    update_history=True,
                )
            self.assertFalse(report["first_run"])
            self.assertEqual(len(report["new_alerts"]), 0)
            alert.assert_not_called()


class CacheReaderTests(unittest.TestCase):
    def test_parse_cache_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "63403.json"
            path.write_text(
                json.dumps(
                    {
                        "counter_id": 63403,
                        "name": "divaninfo.ru",
                        "series": [{"date": "2026-08-21", "visits": 500, "users": 400}],
                    }
                ),
                encoding="utf-8",
            )
            series = ma.parse_cache_file(path)
            self.assertIsNotNone(series)
            assert series is not None
            self.assertEqual(series.name, "divaninfo.ru")
            self.assertEqual(series.points[0].visitors, 400.0)


class CliDryRunTests(unittest.TestCase):
    def test_default_cli_is_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = ma.cache_dir(root)
            cache.mkdir(parents=True)
            start = date(2026, 8, 1)
            payload = {
                "counter_id": 1,
                "name": "amadey.ru",
                "daily": [
                    {
                        "date": (start + timedelta(days=i)).isoformat(),
                        "visits": 100 if i < 15 else 40,
                        "visitors": 80,
                    }
                    for i in range(16)
                ],
            }
            (cache / "1.json").write_text(json.dumps(payload), encoding="utf-8")

            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                code = ma.main(["--root", str(root), "--date", (start + timedelta(days=15)).isoformat()])
            self.assertEqual(code, 0)
            self.assertIn("amadey.ru", buf.getvalue())
            self.assertFalse(ma.anomalies_path(root).exists())


if __name__ == "__main__":
    unittest.main()
