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

    def test_refresh_writes_tool_index_json(self) -> None:
        root = self._build_repo()
        (root / "scripts" / "alpha_tool.py").write_text(
            '"""Alpha utility."""\n',
            encoding="utf-8",
        )
        skills = root / "src" / "skills"
        skills.mkdir(parents=True)
        (skills / "__init__.py").write_text('"""skills"""', encoding="utf-8")
        (skills / "beta_skill.py").write_text(
            '"""Beta skill module."""\n',
            encoding="utf-8",
        )

        index_path = root / "scripts" / "tool_index.json"
        prev_out = sys.stdout
        try:
            sys.stdout = io.StringIO()
            exit_code = tool_discovery.main(
                ["--root", str(root), "--index", str(index_path), "--refresh"]
            )
        finally:
            sys.stdout = prev_out

        self.assertEqual(exit_code, 0)
        self.assertTrue(index_path.exists())
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("version"), tool_discovery.INDEX_VERSION)
        paths = {item["path"] for item in payload["items"]}
        self.assertIn("scripts/alpha_tool.py", paths)
        self.assertIn("src/skills/beta_skill.py", paths)

    def test_search_ranks_matching_paths(self) -> None:
        root = self._build_repo()
        (root / "scripts" / "obsidian_sync.py").write_text(
            '"""Sync Obsidian vault notes."""\n',
            encoding="utf-8",
        )
        index_path = root / "scripts" / "tool_index.json"
        prev_out = sys.stdout
        try:
            sys.stdout = io.StringIO()
            tool_discovery.main(["--root", str(root), "--index", str(index_path), "--refresh"])
        finally:
            sys.stdout = prev_out

        buffer = io.StringIO()
        previous = sys.stdout
        try:
            sys.stdout = buffer
            exit_code = tool_discovery.main(
                [
                    "--root",
                    str(root),
                    "--index",
                    str(index_path),
                    "--search",
                    "obsidian vault",
                    "--limit",
                    "5",
                ]
            )
        finally:
            sys.stdout = previous

        self.assertEqual(exit_code, 0)
        out = buffer.getvalue()
        self.assertIn("obsidian_sync", out)

    def test_stats_reports_counts(self) -> None:
        root = self._build_repo()
        (root / "scripts" / "tiny_tool.py").write_text("# tiny\n", encoding="utf-8")
        index_path = root / "scripts" / "tool_index.json"
        prev_out = sys.stdout
        try:
            sys.stdout = io.StringIO()
            tool_discovery.main(["--root", str(root), "--index", str(index_path), "--refresh"])
        finally:
            sys.stdout = prev_out

        buffer = io.StringIO()
        previous = sys.stdout
        try:
            sys.stdout = buffer
            exit_code = tool_discovery.main(
                ["--root", str(root), "--index", str(index_path), "--stats"]
            )
        finally:
            sys.stdout = previous

        self.assertEqual(exit_code, 0)
        self.assertIn("Total entries:", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
