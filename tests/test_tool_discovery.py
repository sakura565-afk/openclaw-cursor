from __future__ import annotations

import io
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import tool_discovery  # noqa: E402


class ToolDiscoveryTests(unittest.TestCase):
    def _build_repo(self) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        scripts_dir = root / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "__init__.py").write_text('"""scripts"""', encoding="utf-8")
        return root

    def test_analyze_scripts_infers_capabilities_and_dependencies(self) -> None:
        root = self._build_repo()
        (root / "scripts" / "queue_monitor.py").write_text(
            textwrap.dedent(
                """
                import argparse
                import json
                import requests
                import subprocess

                def monitor_queue():
                    return 1

                def parse_args():
                    parser = argparse.ArgumentParser()
                    subs = parser.add_subparsers(dest="cmd")
                    subs.add_parser("watch")
                    subs.add_parser("report")
                    return parser.parse_args()
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        (root / "scripts" / "queue_analytics.py").write_text(
            textwrap.dedent(
                """
                import argparse
                import json
                import pathlib

                def build_report():
                    return {}

                def parse_args():
                    parser = argparse.ArgumentParser()
                    subs = parser.add_subparsers(dest="cmd")
                    subs.add_parser("report")
                    return parser.parse_args()
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        profiles = tool_discovery.analyze_scripts(root)
        by_name = {profile.name: profile for profile in profiles}
        self.assertIn("queue_monitor", by_name)
        self.assertIn("queue_analytics", by_name)
        self.assertEqual(by_name["queue_monitor"].risk_level, "high")
        self.assertIn("Queue orchestration", by_name["queue_monitor"].capabilities)
        self.assertIn("queue_analytics", by_name["queue_monitor"].dependencies)

    def test_generate_markdown_includes_examples_and_dependencies(self) -> None:
        profile = tool_discovery.ToolProfile(
            name="sync_obsidian",
            path=Path("scripts/sync_obsidian.py"),
            description="Sync Obsidian notes",
            commands=["sync"],
            capabilities=["Data synchronization"],
            io_profile=["filesystem", "network"],
            dependencies=["telegram_sender"],
            examples=["python -m scripts.sync_obsidian sync"],
        )

        markdown = tool_discovery.generate_markdown([profile])
        self.assertIn("### Example usage", markdown)
        self.assertIn("telegram_sender", markdown)
        self.assertIn("python -m scripts.sync_obsidian sync", markdown)

    def test_suggest_tools_returns_contextual_reasoning(self) -> None:
        profiles = [
            tool_discovery.ToolProfile(
                name="queue_manager",
                path=Path("scripts/queue_manager.py"),
                description="Manage queue workload",
                commands=["list", "watch"],
                capabilities=["Queue orchestration", "Monitoring and observability"],
                io_profile=["filesystem"],
                dependencies=["memory_analytics"],
            ),
            tool_discovery.ToolProfile(
                name="telegram_sender",
                path=Path("scripts/telegram_sender.py"),
                description="Send notifications",
                commands=["send"],
                capabilities=["Messaging and notifications"],
                io_profile=["network"],
            ),
        ]

        suggestions = tool_discovery.suggest_tools(
            profiles,
            goal="monitor queue latency and generate report",
            context="need safe local file logs",
            top_n=2,
        )
        self.assertEqual(len(suggestions), 2)
        self.assertEqual(suggestions[0]["tool"], "queue_manager")
        reasons = " ".join(suggestions[0]["reasoning"])
        self.assertIn("Capability match", reasons)
        self.assertIn("I/O fit", reasons)

    def test_main_docs_command_writes_file(self) -> None:
        root = self._build_repo()
        (root / "scripts" / "tiny_tool.py").write_text(
            textwrap.dedent(
                """
                import argparse

                def parse_args():
                    parser = argparse.ArgumentParser()
                    subs = parser.add_subparsers(dest="cmd")
                    subs.add_parser("run")
                    return parser.parse_args()
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        output_path = root / "docs" / "tools.md"
        exit_code = tool_discovery.main(
            ["--root", str(root), "docs", "--output", str(output_path)]
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(output_path.exists())
        self.assertIn("tiny_tool", output_path.read_text(encoding="utf-8"))

    def test_main_suggest_prints_json(self) -> None:
        root = self._build_repo()
        (root / "scripts" / "notify_tool.py").write_text(
            textwrap.dedent(
                """
                import argparse
                import requests

                def parse_args():
                    parser = argparse.ArgumentParser()
                    subs = parser.add_subparsers(dest="cmd")
                    subs.add_parser("send")
                    return parser.parse_args()
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        buffer = io.StringIO()
        previous = sys.stdout
        try:
            sys.stdout = buffer
            exit_code = tool_discovery.main(
                [
                    "--root",
                    str(root),
                    "suggest",
                    "send notification",
                    "--context",
                    "api webhook",
                    "--top",
                    "1",
                ]
            )
        finally:
            sys.stdout = previous

        self.assertEqual(exit_code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["goal"], "send notification")
        self.assertEqual(len(payload["suggestions"]), 1)

    def test_build_full_index_includes_python_and_markdown(self) -> None:
        root = self._build_repo()
        skills_dir = root / "src" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "alert_skill.py").write_text(
            '"""Watch for alerts."""\nimport json\ndef run():\n    pass\n',
            encoding="utf-8",
        )
        skill_md = root / "nested" / "SKILL.md"
        skill_md.parent.mkdir(parents=True)
        skill_md.write_text(
            "---\nname: Demo Skill\ndescription: Send demo notifications\ncapabilities:\n  - messaging\n---\n\nBody.\n",
            encoding="utf-8",
        )
        data = tool_discovery.build_full_index(root)
        kinds = {e["kind"] for e in data["entries"]}
        self.assertIn("skill_module", kinds)
        self.assertIn("skill_markdown", kinds)
        self.assertTrue(data["keyword_index"])

    def test_search_index_matches_keywords(self) -> None:
        data: dict = {
            "entries": [
                {
                    "id": "a",
                    "kind": "script",
                    "name": "notify_tool",
                    "path": "scripts/notify_tool.py",
                    "description": "send alerts",
                    "search_text": "notify_tool send alerts telegram network",
                }
            ]
        }
        hits = tool_discovery.search_index(data, "send telegram", limit=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["id"], "a")
        self.assertGreater(hits[0]["match_score"], 0)

    def test_main_search_prints_json(self) -> None:
        root = self._build_repo()
        (root / "scripts" / "queue_watch.py").write_text(
            textwrap.dedent(
                '''
                """Monitor queues."""
                import json
                def watch():
                    return 1
                '''
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        buffer = io.StringIO()
        previous = sys.stdout
        try:
            sys.stdout = buffer
            exit_code = tool_discovery.main(
                ["--root", str(root), "search", "monitor queue", "--limit", "5"]
            )
        finally:
            sys.stdout = previous
        self.assertEqual(exit_code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertIn("matches", payload)
        self.assertTrue(payload["matches"])

    def test_main_index_writes_json_file(self) -> None:
        root = self._build_repo()
        (root / "scripts" / "tiny_tool.py").write_text(
            "'''One-liner.'''\n",
            encoding="utf-8",
        )
        out = root / "idx.json"
        code = tool_discovery.main(["--root", str(root), "index", "-o", str(out)])
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], tool_discovery.INDEX_VERSION)
        self.assertTrue(any(e["name"] == "tiny_tool" for e in payload["entries"]))


if __name__ == "__main__":
    unittest.main()
