from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.tool_discovery import ToolUsageTracker, generate_tool_docs, scan_openclaw_tools  # noqa: E402


class ToolDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "scripts").mkdir()
        (self.root / "src" / "pkg").mkdir(parents=True)
        (self.root / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "src" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_scan_discovers_cli_commands_and_public_functions(self) -> None:
        (self.root / "scripts" / "demo_tool.py").write_text(
            "\n".join(
                [
                    '"""Demo tool for discovery."""',
                    "",
                    "from __future__ import annotations",
                    "",
                    "import argparse",
                    "",
                    "def helper() -> str:",
                    '    return "ok"',
                    "",
                    "def build_parser() -> argparse.ArgumentParser:",
                    '    parser = argparse.ArgumentParser(description="Demo CLI")',
                    '    subparsers = parser.add_subparsers(dest="command", required=True)',
                    '    subparsers.add_parser("scan", help="Run a scan.")',
                    '    subparsers.add_parser("docs", help="Show docs.")',
                    "    return parser",
                ]
            ),
            encoding="utf-8",
        )
        (self.root / "src" / "pkg" / "loop_tool.py").write_text(
            "\n".join(
                [
                    '"""Loop-generated command definitions."""',
                    "",
                    "from __future__ import annotations",
                    "",
                    "import argparse",
                    "",
                    "def build_parser() -> argparse.ArgumentParser:",
                    '    parser = argparse.ArgumentParser(description="Loop CLI")',
                    '    subparsers = parser.add_subparsers(dest="command", required=True)',
                    '    for command in ("usage", "suggest"):',
                    "        subparsers.add_parser(command)",
                    "    return parser",
                    "",
                    "class ToolBox:",
                    "    pass",
                ]
            ),
            encoding="utf-8",
        )

        tools = scan_openclaw_tools(self.root)

        by_name = {tool.name: tool for tool in tools}
        self.assertEqual(set(by_name), {"scripts.demo_tool", "src.pkg.loop_tool"})
        self.assertEqual([command.name for command in by_name["scripts.demo_tool"].commands], ["docs", "scan"])
        self.assertEqual(by_name["scripts.demo_tool"].functions, ["build_parser", "helper"])
        self.assertEqual([command.name for command in by_name["src.pkg.loop_tool"].commands], ["suggest", "usage"])
        self.assertEqual(by_name["src.pkg.loop_tool"].classes, ["ToolBox"])

    def test_generate_docs_and_usage_tracker_persist_artifacts(self) -> None:
        (self.root / "scripts" / "demo_tool.py").write_text(
            "\n".join(
                [
                    '"""A compact tool used in tests."""',
                    "",
                    "from __future__ import annotations",
                    "",
                    "import argparse",
                    "",
                    "def run() -> int:",
                    "    return 0",
                    "",
                    "def build_parser() -> argparse.ArgumentParser:",
                    '    parser = argparse.ArgumentParser(description="Compact CLI")',
                    '    subparsers = parser.add_subparsers(dest="command", required=True)',
                    '    subparsers.add_parser("alpha", help="Alpha command.")',
                    "    return parser",
                ]
            ),
            encoding="utf-8",
        )

        tools = scan_openclaw_tools(self.root)
        written = generate_tool_docs(tools, root=self.root)

        self.assertIn("scripts.demo_tool", written)
        doc_path = written["scripts.demo_tool"]
        self.assertTrue(doc_path.exists())
        doc_text = doc_path.read_text(encoding="utf-8")
        self.assertIn("# scripts.demo_tool", doc_text)
        self.assertIn("`alpha` - Alpha command.", doc_text)

        tracker = ToolUsageTracker(root=self.root)
        tracker.record_command("scan")
        tracker.record_command("docs", tool_name="scripts.demo_tool")
        tracker.record_command("docs", tool_name="scripts.demo_tool")

        snapshot = json.loads((self.root / ".learnings" / "tool_discovery" / "usage.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["commands"]["scan"], 1)
        self.assertEqual(snapshot["commands"]["docs"], 2)

        most_used = tracker.most_used_tools(limit=1)
        self.assertEqual(most_used[0]["tool_name"], "scripts.demo_tool")
        self.assertEqual(most_used[0]["views"], 2)

        suggestions = tracker.recommend_underused_tools(tools, limit=3)
        self.assertEqual(suggestions[0]["tool_name"], "scripts.demo_tool")
        self.assertIn("tracked views", suggestions[0]["reason"])


if __name__ == "__main__":
    unittest.main()
