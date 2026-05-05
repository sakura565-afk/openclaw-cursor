import unittest
from unittest.mock import MagicMock, patch

from src.monitoring.health_check import (
    DEFAULT_SESSION_THRESHOLD,
    _extract_session_percentages,
    run_health_check,
)


class HealthCheckTests(unittest.TestCase):
    def test_extract_session_percentages_ignores_non_numeric(self):
        payload = {
            "sessions": {
                "recent": [
                    {"key": "a", "percentUsed": 50},
                    {"key": "b", "percentUsed": 82.5},
                    {"key": "c", "percentUsed": "n/a"},
                    {"key": "d"},
                ]
            }
        }

        result = _extract_session_percentages(payload)
        self.assertEqual(result, [50.0, 82.5])

    @patch("src.monitoring.health_check.check_gateway_health")
    @patch("src.monitoring.health_check.check_openclaw_status")
    def test_alerts_when_gateway_down(
        self,
        mock_openclaw: MagicMock,
        mock_gateway: MagicMock,
    ):
        mock_openclaw.return_value = {"ok": True, "sessions_percent": [20.0]}
        mock_gateway.return_value = {"ok": False, "error": "connection refused"}

        report = run_health_check()

        self.assertTrue(report.openclaw_ok)
        self.assertFalse(report.gateway_ok)
        self.assertIn("Gateway health check failed", report.alerts)

    @patch("src.monitoring.health_check.check_gateway_health")
    @patch("src.monitoring.health_check.check_openclaw_status")
    def test_alerts_when_session_over_threshold(
        self,
        mock_openclaw: MagicMock,
        mock_gateway: MagicMock,
    ):
        mock_openclaw.return_value = {
            "ok": True,
            "sessions_percent": [DEFAULT_SESSION_THRESHOLD + 1.0],
        }
        mock_gateway.return_value = {"ok": True, "status_code": 200}

        report = run_health_check()

        self.assertTrue(report.gateway_ok)
        self.assertEqual(len(report.alerts), 1)
        self.assertIn("Session usage high", report.alerts[0])


if __name__ == "__main__":
    unittest.main()
