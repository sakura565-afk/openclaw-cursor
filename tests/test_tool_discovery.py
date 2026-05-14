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

    def test_scan_populates_registry_and_markdown(self) -> None:
        root = self._build_repo()
        (root / "src").mkdir(parents=True)
        (root / "src" / "alpha_client.py").write_text(
            textwrap.dedent(
                '''
                """HTTP helper for the Alpha observability API."""

                import argparse
                import requests

                class AlphaClient:
                    """Thin wrapper around the Alpha REST API."""

                    def get(self, path: str) -> str:
                        return requests.get(path).text

                def run_cli():
                    """Run the Alpha CLI entrypoint with argparse-backed flags."""
                    parser = argparse.ArgumentParser()
                    parser.parse_args()

                if __name__ == "__main__":
                    run_cli()
                '''
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        reg = root / "tools_inventory.json"
        md = root / "TOOLS_INVENTORY.md"
        exit_code = tool_discovery.main(
            [
                "--root",
                str(root),
                "--registry",
                str(reg),
                "scan",
                "--no-markdown",
            ]
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(reg.exists())
        _, tools = tool_discovery.load_registry(reg)
        kinds = {t.kind for t in tools}
        self.assertIn("module", kinds)
        self.assertIn("class", kinds)
        self.assertIn("function", kinds)

        exit_code = tool_discovery.main(
            ["--root", str(root), "--registry", str(reg), "scan", "--markdown-output", str(md)]
        )
        self.assertEqual(exit_code, 0)
        text = md.read_text(encoding="utf-8")
        self.assertIn("AlphaClient", text)
        self.assertIn("## Index", text)

    def test_add_tool_and_search(self) -> None:
        root = self._build_repo()
        reg = root / "tools_inventory.json"
        (root / "scripts" / "stub.py").write_text('"""stub"""\n', encoding="utf-8")
        tool_discovery.main(["--root", str(root), "--registry", str(reg), "scan", "--no-markdown"])
        code = tool_discovery.main(
            [
                "--root",
                str(root),
                "--registry",
                str(reg),
                "add-tool",
                "--name",
                "Custom helper",
                "--description",
                "Does custom analytics for queue dashboards",
                "--path",
                "scripts/stub.py",
                "--example",
                "python -m scripts.stub --help",
            ]
        )
        self.assertEqual(code, 0)
        buf = io.StringIO()
        prev = sys.stdout
        try:
            sys.stdout = buf
            tool_discovery.main(
                ["--root", str(root), "--registry", str(reg), "search-tools", "analytics"]
            )
        finally:
            sys.stdout = prev
        self.assertIn("Custom helper", buf.getvalue())

    def test_match_skills_links_inventory_to_skill_files(self) -> None:
        root = self._build_repo()
        skill_dir = root / "skills" / "demo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "# Queue analytics playbook\n\nUse queue dashboards and analytics exports.\n",
            encoding="utf-8",
        )
        (root / "scripts" / "queue_board.py").write_text(
            textwrap.dedent(
                '''
                """Dashboards for queue analytics."""

                import argparse

                if __name__ == "__main__":
                    argparse.ArgumentParser().parse_args()
                '''
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        reg = root / "tools_inventory.json"
        tool_discovery.main(["--root", str(root), "--registry", str(reg), "scan", "--no-markdown"])
        _, tools = tool_discovery.load_registry(reg)
        tool_discovery.attach_skill_hints(root, tools)
        board = next(t for t in tools if t.name == "queue_board")
        self.assertTrue(any("SKILL.md" in h["path"] for h in board.suggested_skills))

    def test_docs_from_registry_writes_markdown(self) -> None:
        root = self._build_repo()
        (root / "scripts" / "solo.py").write_text(
            textwrap.dedent(
                '''
                """Solo runner."""
                import argparse
                if __name__ == "__main__":
                    argparse.ArgumentParser().parse_args()
                '''
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        reg = root / "tools_inventory.json"
        tool_discovery.main(["--root", str(root), "--registry", str(reg), "scan", "--no-markdown"])
        out = root / "registry_docs.md"
        code = tool_discovery.main(
            [
                "--root",
                str(root),
                "--registry",
                str(reg),
                "docs",
                "--from-registry",
                "--output",
                str(out),
            ]
        )
        self.assertEqual(code, 0)
        body = out.read_text(encoding="utf-8")
        self.assertIn("Discovered tools documentation", body)
        self.assertIn("solo", body)


if __name__ == "__main__":
    unittest.main()
