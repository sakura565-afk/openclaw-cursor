from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.memory_cleanup import cleanup_memory  # noqa: E402


class MemoryCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "memory").mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write(self, relative_path: str, content: str) -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def read(self, relative_path: str) -> str:
        return (self.root / relative_path).read_text(encoding="utf-8")

    def test_cleanup_archives_duplicates_and_similar_entries(self) -> None:
        self.write(
            "MEMORY.md",
            "\n".join(
                [
                    "# Memory",
                    "",
                    "## Alpha current",
                    "Updated: 2026-05-01",
                    "Keep this active note.",
                    "",
                    "## Alpha duplicate old",
                    "Updated: 2026-04-30",
                    "Keep this active note.",
                    "",
                    "## Similar primary",
                    "Updated: 2026-05-02",
                    "Shared line one.",
                    "Shared line two.",
                    "Primary only detail.",
                    "",
                    "## Similar duplicate",
                    "Updated: 2026-05-01",
                    "Shared line one.",
                    "Shared line two.",
                    "Secondary only detail.",
                    "",
                    "## Stale entry",
                    "Updated: 2025-12-01",
                    "This should move to the archive.",
                    "",
                ]
            )
            + "\n",
        )
        self.write(
            "memory/2026-05-04.md",
            "\n".join(
                [
                    "## Daily recent",
                    "Updated: 2026-05-04",
                    "Recent note.",
                    "",
                ]
            ),
        )

        report = cleanup_memory(
            root=self.root,
            days=90,
            dry_run=False,
            backup=True,
            today=date(2026, 5, 4),
            now=datetime(2026, 5, 4, 12, 30, 0),
        )

        memory_text = self.read("MEMORY.md")
        self.assertIn("## Alpha current", memory_text)
        self.assertNotIn("## Alpha duplicate old", memory_text)
        self.assertIn("Primary only detail.", memory_text)
        self.assertIn("Secondary only detail.", memory_text)
        self.assertNotIn("## Stale entry", memory_text)

        archive_dir = self.root / "memory" / "archive"
        archive_files = list(archive_dir.glob("*.md"))
        self.assertEqual(len(archive_files), 1)
        archive_text = archive_files[0].read_text(encoding="utf-8")
        self.assertIn("Stale entry", archive_text)
        self.assertIn("This should move to the archive.", archive_text)

        backup_path = self.root / "memory" / "MEMORY.md.backup_20260504"
        self.assertTrue(backup_path.exists())
        self.assertIn("## Alpha duplicate old", backup_path.read_text(encoding="utf-8"))

        self.assertEqual(report["duplicates_removed"], 1)
        self.assertEqual(report["similar_entries_compacted"], 1)
        self.assertEqual(report["archived_entries"], 1)
        self.assertIn("memory/MEMORY.md.backup_20260504", report["backup_files"])

        weekly_summary = json.loads(
            self.read("logs/weekly_summary_2026-W19.json")
        )
        self.assertEqual(weekly_summary["aggregate"]["archived_entries"], 1)
        self.assertEqual(weekly_summary["aggregate"]["duplicates_removed"], 1)
        self.assertEqual(weekly_summary["aggregate"]["similar_entries_compacted"], 1)

        log_report = json.loads(
            self.read("logs/cleanup_report_20260504_123000.json")
        )
        self.assertEqual(log_report["entries_before"], 6)
        self.assertEqual(log_report["entries_after"], 3)

    def test_dry_run_preserves_sources_but_writes_logs_and_backup(self) -> None:
        original = "\n".join(
            [
                "## Keep",
                "Updated: 2026-05-02",
                "Fresh note.",
                "",
                "## Old",
                "Updated: 2025-01-01",
                "Archive me.",
                "",
            ]
        )
        self.write("MEMORY.md", original)

        report = cleanup_memory(
            root=self.root,
            days=90,
            dry_run=True,
            backup=True,
            today=date(2026, 5, 4),
            now=datetime(2026, 5, 4, 8, 0, 0),
        )

        self.assertEqual(self.read("MEMORY.md"), original)
        self.assertTrue((self.root / "memory" / "MEMORY.md.backup_20260504").exists())
        self.assertFalse((self.root / "memory" / "archive").exists())
        self.assertTrue((self.root / "logs" / "cleanup_report_20260504_080000.json").exists())
        self.assertTrue(report["dry_run"])

    def test_cli_outputs_colorized_summary(self) -> None:
        self.write(
            "MEMORY.md",
            "\n".join(
                [
                    "## Duplicate one",
                    "Updated: 2026-05-04",
                    "Same details.",
                    "",
                    "## Duplicate two",
                    "Updated: 2026-05-03",
                    "Same details.",
                    "",
                ]
            ),
        )

        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "memory_cleanup.py"), "--dry-run", "--backup"],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("\x1b[", result.stdout)
        self.assertIn("OpenClaw memory cleanup", result.stdout)
        self.assertTrue((self.root / "memory" / "MEMORY.md.backup_").parent.exists())
        log_files = list((self.root / "logs").glob("cleanup_report_*.json"))
        self.assertEqual(len(log_files), 1)


if __name__ == "__main__":
    unittest.main()
