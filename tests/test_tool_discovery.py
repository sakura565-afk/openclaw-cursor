import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.self_improvement.tool_discovery import ToolCatalogEntry, ToolDiscoveryEngine


class ToolDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.logs = Path(self.temp_dir.name) / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)

    def make_engine(self):
        catalog = {
            "Read": ToolCatalogEntry(
                name="Read",
                description="Read a file",
                tags=("files", "read"),
                task_types=("debug", "implement"),
            ),
            "Grep": ToolCatalogEntry(
                name="Grep",
                description="Search code",
                tags=("search", "code"),
                task_types=("debug", "explore"),
            ),
            "Shell": ToolCatalogEntry(
                name="Shell",
                description="Run commands",
                tags=("commands",),
                task_types=(),
            ),
        }
        return ToolDiscoveryEngine(log_dir=self.logs, tool_catalog=catalog)

    def test_analyze_usage_builds_matrix_and_success_rates(self):
        engine = self.make_engine()
        fixed = datetime(2099, 1, 1, tzinfo=timezone.utc)
        engine.record_usage(tool="Read", task_type="debug", success=True, skills=["python"], timestamp=fixed)
        engine.record_usage(tool="Read", task_type="debug", success=False, skills=["python"], timestamp=fixed)
        engine.record_usage(tool="Grep", task_type="debug", success=True, skills=["regex"], timestamp=fixed)

        snapshot = engine.analyze_usage(since_days=None)
        self.assertEqual(snapshot.tool_usage_counts["Read"], 2)
        self.assertEqual(snapshot.tool_usage_counts["Grep"], 1)
        self.assertEqual(snapshot.skill_usage_counts["python"], 2)
        self.assertEqual(snapshot.tool_success_rates["Read"]["total"], 2)
        self.assertAlmostEqual(snapshot.tool_success_rates["Read"]["success_rate"], 0.5, places=4)
        self.assertIn("debug", snapshot.usage_matrix["Read"])
        self.assertEqual(snapshot.usage_matrix["Read"]["debug"]["total"], 2)

    def test_generate_markdown_contains_recommendations_and_matrix(self):
        engine = self.make_engine()
        fixed = datetime(2099, 1, 1, tzinfo=timezone.utc)
        engine.record_usage(tool="Read", task_type="debug", success=True, timestamp=fixed)
        engine.record_usage(tool="Read", task_type="debug", success=True, timestamp=fixed)
        engine.record_usage(tool="Grep", task_type="debug", success=False, timestamp=fixed)

        report = engine.generate_recommendations_markdown(
            task_type="debug",
            context_text="need to read files and search code",
            since_days=None,
            matrix_days=None,
            top_n=3,
        )
        self.assertIn("# Tool Recommendations (debug)", report)
        self.assertIn("## Recommended tools", report)
        self.assertIn("## Tool usage matrix (success rate)", report)
        self.assertIn("| Tool |", report)
        self.assertIn("Read", report)

    def test_write_dashboard_snapshot_writes_json(self):
        engine = self.make_engine()
        fixed = datetime(2099, 1, 1, tzinfo=timezone.utc)
        engine.record_usage(tool="Shell", task_type="implement", success=True, timestamp=fixed)
        path = engine.write_dashboard_snapshot(since_days=None)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("tool_usage_counts", payload)
        self.assertEqual(payload["tool_usage_counts"]["Shell"], 1)


if __name__ == "__main__":
    unittest.main()

