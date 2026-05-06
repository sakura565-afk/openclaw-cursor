import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from src.self_improvement.tool_discovery import ToolDiscoverySystem


class ToolDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        (self.root / "src" / "skills").mkdir(parents=True, exist_ok=True)
        (self.root / "scripts").mkdir(parents=True, exist_ok=True)
        (self.root / "docs").mkdir(parents=True, exist_ok=True)
        (self.root / "logs").mkdir(parents=True, exist_ok=True)

        (self.root / "src" / "skills" / "proactive_watcher.py").write_text(
            "def run_watcher():\n    return True\n\n"
            "def monitor_queue():\n    return 'ok'\n",
            encoding="utf-8",
        )
        (self.root / "scripts" / "cleanup_tool.py").write_text(
            "def execute_cleanup():\n    return 'done'\n",
            encoding="utf-8",
        )
        (self.root / "scripts" / "old_manager.py").write_text(
            "def legacy():\n    return None\n",
            encoding="utf-8",
        )
        (self.root / "README.md").write_text(
            "Tools:\n- cleanup_tool\n", encoding="utf-8"
        )

        old = datetime.now(timezone.utc) - timedelta(days=5)
        old_ts = old.timestamp()
        old_path = self.root / "scripts" / "old_manager.py"
        # Keep one file outside recent window.
        os_utime = __import__("os").utime
        os_utime(old_path, (old_ts, old_ts))

    def test_discover_detects_recent_undocumented_and_unused(self):
        system = ToolDiscoverySystem(
            workspace_root=self.root,
            state_dir=self.root / "logs",
            recent_hours=72,
            new_hours=24,
        )

        snapshot = system.discover()
        self.assertGreaterEqual(snapshot.total_tools, 3)

        names_recent = {tool.name for tool in snapshot.recent_tools}
        self.assertIn("cleanup_tool", names_recent)
        self.assertIn("proactive_watcher", names_recent)
        self.assertNotIn("old_manager", names_recent)

        undocumented_names = {tool.name for tool in snapshot.undocumented_tools}
        self.assertIn("proactive_watcher", undocumented_names)

        unused_names = {tool.name for tool in snapshot.unused_tools}
        self.assertIn("old_manager", unused_names)

        self.assertIn("top_skills", snapshot.skill_usage)
        self.assertTrue(snapshot.suggestions)

    def test_report_and_evolution_are_persisted(self):
        system = ToolDiscoverySystem(workspace_root=self.root, state_dir=self.root / "logs")
        with mock.patch("src.self_improvement.tool_discovery._now_utc", return_value=datetime(2099, 1, 1, tzinfo=timezone.utc)):
            snapshot = system.discover()
            payload = system.generate_report(snapshot=snapshot, write_files=True)

        self.assertIn("json_path", payload)
        self.assertIn("markdown_path", payload)
        self.assertTrue(Path(payload["json_path"]).exists())
        self.assertTrue(Path(payload["markdown_path"]).exists())

        history = system.tool_evolution(limit=5)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["total_tools"], snapshot.total_tools)

        report_json = json.loads(Path(payload["json_path"]).read_text(encoding="utf-8"))
        self.assertEqual(report_json["total_tools"], snapshot.total_tools)


if __name__ == "__main__":
    unittest.main()
