import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from scripts.auto_reflection import AutoReflectionAnalyzer


class AutoReflectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.logs = Path(self.temp_dir.name)

    def _write_entries(self, day: str, entries):
        path = self.logs / f"auto_reflection_{day}.json"
        path.write_text(json.dumps(entries), encoding="utf-8")

    def test_analyze_builds_patterns_learning_and_trends(self):
        self._write_entries(
            "20990101",
            [
                {
                    "timestamp": "2099-01-01T01:00:00+00:00",
                    "session_id": "session-a",
                    "decision": "restart_ollama",
                    "outcome": "success",
                    "confidence": 0.6,
                    "impact": 0.4,
                },
                {
                    "timestamp": "2099-01-01T02:00:00+00:00",
                    "session_id": "session-a",
                    "decision": "trim_context",
                    "outcome": "failed",
                    "confidence": 0.5,
                    "impact": -0.2,
                },
                {
                    "timestamp": "2099-01-02T03:00:00+00:00",
                    "session_id": "session-b",
                    "decision": "restart_ollama",
                    "outcome": "success",
                    "confidence": 0.8,
                    "impact": 0.7,
                },
                {
                    "timestamp": "2099-01-03T03:00:00+00:00",
                    "session_id": "session-c",
                    "decision": "trim_context",
                    "outcome": "failed",
                    "confidence": 0.4,
                    "impact": -0.3,
                },
            ],
        )

        analyzer = AutoReflectionAnalyzer(log_dir=self.logs)
        with mock.patch(
            "scripts.auto_reflection._now_utc",
            return_value=datetime(2099, 1, 3, 12, 0, tzinfo=timezone.utc),
        ):
            analysis = analyzer.analyze(since_days=7)

        self.assertEqual(analysis["entry_count"], 4)
        self.assertEqual(analysis["session_count"], 3)
        self.assertEqual(analysis["decision_patterns"][0]["decision"], "restart_ollama")
        self.assertEqual(analysis["decision_patterns"][0]["success_rate"], 100.0)
        self.assertEqual(analysis["trends"]["success_rate"], "flat")

        learning = analysis["cross_session_learning"]
        self.assertEqual(learning["strongest_patterns"][0]["decision"], "restart_ollama")
        self.assertEqual(learning["volatile_patterns"][0]["decision"], "trim_context")

    def test_render_digest_is_readable_and_structured(self):
        analyzer = AutoReflectionAnalyzer(log_dir=self.logs)
        analysis = {
            "generated_at": "2099-01-03T12:00:00+00:00",
            "since_days": 7,
            "entry_count": 2,
            "session_count": 2,
            "decision_patterns": [
                {
                    "decision": "restart_ollama",
                    "count": 2,
                    "success_rate": 100.0,
                    "avg_confidence": 0.7,
                    "avg_impact": 0.6,
                }
            ],
            "cross_session_learning": {
                "strongest_patterns": [
                    {"decision": "restart_ollama", "success_rate": 100.0, "session_span": 2}
                ],
                "volatile_patterns": [],
            },
            "trends": {
                "volume": "up",
                "success_rate": "up",
                "impact": "up",
                "daily": [{"day": "2099-01-03", "volume": 2, "success_rate": 100.0, "avg_impact": 0.6}],
            },
        }

        digest = analyzer.render_digest(analysis)
        self.assertIn("# Auto Reflection Digest", digest)
        self.assertIn("## Decision Patterns", digest)
        self.assertIn("## Cross-Session Learning", digest)
        self.assertIn("## Trend Detection", digest)
        self.assertIn("| Decision | Count | Success % |", digest)
        self.assertIn("volume=up, success=up, impact=up", digest)

    def test_run_persists_digest_analysis_and_learning_state(self):
        self._write_entries(
            "20990104",
            [
                {
                    "timestamp": "2099-01-04T02:00:00+00:00",
                    "session_id": "s-1",
                    "decision": "restart_ollama",
                    "outcome": "success",
                },
                {
                    "timestamp": "2099-01-04T03:00:00+00:00",
                    "session_id": "s-2",
                    "decision": "restart_ollama",
                    "outcome": "success",
                },
            ],
        )
        analyzer = AutoReflectionAnalyzer(log_dir=self.logs)
        with mock.patch(
            "scripts.auto_reflection._now_utc",
            return_value=datetime(2099, 1, 4, 12, 0, tzinfo=timezone.utc),
        ):
            analysis, digest = analyzer.run(since_days=14)

        self.assertIn("restart_ollama", digest)
        self.assertTrue((self.logs / "auto_reflection_digest.md").exists())
        self.assertTrue((self.logs / "auto_reflection_analysis.json").exists())
        self.assertTrue((self.logs / "auto_reflection_learning.json").exists())
        self.assertEqual(analysis["cross_session_learning"]["run_count"], 1)


if __name__ == "__main__":
    unittest.main()
