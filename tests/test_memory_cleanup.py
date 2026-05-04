import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "memory_cleanup.py"
SPEC = importlib.util.spec_from_file_location("memory_cleanup", MODULE_PATH)
memory_cleanup = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = memory_cleanup
SPEC.loader.exec_module(memory_cleanup)


class MemoryCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "memory").mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_file(self, relative_path: str, content: str) -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def read_file(self, relative_path: str) -> str:
        return (self.root / relative_path).read_text(encoding="utf-8")

    def test_cleanup_archives_deduplicates_and_compacts(self) -> None:
        self.write_file(
            "MEMORY.md",
            "# Team memory\n\n"
            "## Keep recent duplicate\n"
            "Updated: 2026-04-01\n\n"
            "Alpha context line\n"
            "Shared note\n\n"
            "## Archive me\n"
            "Updated: 2025-12-01\n\n"
            "Very old context\n",
        )
        self.write_file(
            "memory/2026-04-28.md",
            "## Duplicate older\n"
            "Updated: 2026-03-15\n\n"
            "Alpha context line\n"
            "Shared note\n\n"
            "## Similar context\n"
            "Updated: 2026-04-20\n\n"
            "Project checkpoint complete\n"
            "Next action item ready\n",
        )
        self.write_file(
            "memory/2026-04-29.md",
            "## Similar context follow-up\n"
            "Updated: 2026-04-25\n\n"
            "Project checkpoint complete\n"
            "Next action items ready\n",
        )

        report = memory_cleanup.run_cleanup(
            root=self.root,
            days=90,
            dry_run=False,
            backup=True,
            today=date(2026, 5, 4),
        )

        self.assertEqual(1, len(report["archived_entries"]))
        self.assertEqual(1, len(report["removed_duplicates"]))
        self.assertEqual(1, len(report["merged_entries"]))
        self.assertEqual(3, len(report["backups"]))

        main_text = self.read_file("MEMORY.md")
        self.assertIn("Keep recent duplicate", main_text)
        self.assertNotIn("Archive me", main_text)

        older_daily_text = self.read_file("memory/2026-04-28.md")
        self.assertNotIn("Duplicate older", older_daily_text)
        self.assertIn("Similar context", older_daily_text)
        self.assertIn("Merged duplicate context", older_daily_text)
        self.assertIn("Next action item ready", older_daily_text)
        self.assertIn("Next action items ready", older_daily_text)

        newer_daily_text = self.read_file("memory/2026-04-29.md")
        self.assertEqual("", newer_daily_text)

        archive_files = sorted((self.root / "memory" / "archive").glob("*.md"))
        self.assertEqual(1, len(archive_files))
        archive_text = archive_files[0].read_text(encoding="utf-8")
        self.assertIn("Archive me", archive_text)

        backup_text = self.read_file("memory/MEMORY.md.backup_20260504")
        self.assertIn("Archive me", backup_text)

        report_json = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))
        self.assertEqual(1, len(report_json["archive_files"]))
        self.assertEqual(1, len(report_json["removed_duplicates"]))

        weekly_summary = Path(report["weekly_summary_path"]).read_text(encoding="utf-8")
        self.assertIn("Archived entries: 1", weekly_summary)
        self.assertIn("Duplicates removed: 1", weekly_summary)

    def test_dry_run_keeps_original_files_unchanged(self) -> None:
        original = (
            "## Old note\n"
            "Updated: 2025-01-01\n\n"
            "Archive candidate\n"
        )
        self.write_file("MEMORY.md", original)

        report = memory_cleanup.run_cleanup(
            root=self.root,
            days=90,
            dry_run=True,
            backup=True,
            today=date(2026, 5, 4),
        )

        self.assertTrue(report["dry_run"])
        self.assertEqual(original, self.read_file("MEMORY.md"))
        self.assertFalse((self.root / "memory" / "archive").exists())
        self.assertFalse((self.root / "memory" / "MEMORY.md.backup_20260504").exists())
        self.assertTrue(Path(report["report_path"]).exists())

    def test_cli_returns_success_and_colorized_output(self) -> None:
        self.write_file(
            "MEMORY.md",
            "## Fresh\n"
            "Updated: 2026-05-01\n\n"
            "Useful note\n",
        )

        with tempfile.TemporaryDirectory() as _:
            from io import StringIO
            from contextlib import redirect_stdout

            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = memory_cleanup.main(
                    argv=["--days", "30", "--dry-run"],
                    root=self.root,
                    today=date(2026, 5, 4),
                )

        output = stdout.getvalue()
        self.assertEqual(0, exit_code)
        self.assertIn("\033[36mOpenClaw memory cleanup", output)
        self.assertIn("Threshold: 30 days", output)


if __name__ == "__main__":
    unittest.main()
