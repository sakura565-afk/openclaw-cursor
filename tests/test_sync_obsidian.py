import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import sync_obsidian


class SyncObsidianTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.memory_path = self.root / "MEMORY.md"
        self.vault_path = self.root / "vault"
        self.logs_path = self.root / "logs"
        self.logs_path.mkdir()

        for relative in ("01_Projects", "02_Knowledge", "memory", ".obsidian", "__pycache__"):
            (self.vault_path / relative).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_memory(self, text: str) -> None:
        self.memory_path.write_text(text, encoding="utf-8")

    def set_mtime(self, path: Path, timestamp: datetime) -> None:
        unix_time = timestamp.replace(tzinfo=timezone.utc).timestamp()
        os.utime(path, (unix_time, unix_time))

    def test_daily_note_added_for_new_memory_file(self):
        self.write_memory("# MEMORY\n")
        memory_note = self.vault_path / "memory" / "idea.md"
        memory_note.write_text("# Idea\n", encoding="utf-8")

        result = sync_obsidian.sync_memory_and_vault(
            memory_path=self.memory_path,
            vault_path=self.vault_path,
            dry_run=False,
            log_dir=self.logs_path,
            now=datetime(2026, 5, 4, 10, 30, 0, tzinfo=timezone.utc),
        )

        updated = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("## Daily Notes", updated)
        self.assertIn("- 2026-05-04: Added vault note [[memory/idea]]", updated)
        self.assertEqual(result["stats"]["daily_note_entries_added"], 1)

        report = self.logs_path / "sync_obsidian_20260504.json"
        report_data = json.loads(report.read_text(encoding="utf-8"))
        self.assertFalse(report_data["dry_run"])
        self.assertEqual(report_data["stats"]["daily_note_entries_added"], 1)

    def test_updated_vault_note_marks_memory_section_stale(self):
        self.write_memory(
            "# MEMORY\n\n"
            "## Topic\n"
            "Body.\n"
        )
        self.set_mtime(self.memory_path, datetime(2026, 5, 4, 10, 0, 0))

        note = self.vault_path / "01_Projects" / "topic.md"
        note.write_text("# Topic\nUpdated\n", encoding="utf-8")
        self.set_mtime(note, datetime(2026, 5, 4, 12, 0, 0))

        result = sync_obsidian.sync_memory_and_vault(
            memory_path=self.memory_path,
            vault_path=self.vault_path,
            dry_run=False,
            log_dir=self.logs_path,
            now=datetime(2026, 5, 4, 12, 30, 0, tzinfo=timezone.utc),
        )

        updated = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("> [!warning] Sync stale: `01_Projects/topic.md` changed at 2026-05-04T12:00:00+00:00", updated)
        self.assertTrue(any(action["type"] == "mark_memory_section_stale" for action in result["actions"]))

    def test_memory_section_generates_vault_index_reference(self):
        self.write_memory(
            "# MEMORY\n\n"
            "## Research Topic\n"
            "Notes here.\n"
        )

        result = sync_obsidian.sync_memory_and_vault(
            memory_path=self.memory_path,
            vault_path=self.vault_path,
            dry_run=False,
            log_dir=self.logs_path,
            now=datetime(2026, 5, 4, 9, 0, 0, tzinfo=timezone.utc),
        )

        index_path = self.vault_path / "memory" / "MEMORY_sync_index.md"
        self.assertTrue(index_path.exists())
        index_text = index_path.read_text(encoding="utf-8")
        self.assertIn("- Research Topic", index_text)
        self.assertTrue(any(action["type"] == "update_vault_reference" for action in result["actions"]))

    def test_dry_run_preserves_files_and_logs_preview(self):
        self.write_memory("# MEMORY\n\n## Topic\nBody.\n")

        result = sync_obsidian.sync_memory_and_vault(
            memory_path=self.memory_path,
            vault_path=self.vault_path,
            dry_run=True,
            log_dir=self.logs_path,
            now=datetime(2026, 5, 4, 8, 0, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(self.memory_path.read_text(encoding="utf-8"), "# MEMORY\n\n## Topic\nBody.\n")
        self.assertFalse((self.vault_path / "memory" / "MEMORY_sync_index.md").exists())
        self.assertGreaterEqual(len(result["actions"]), 1)
        self.assertTrue(all(action["status"] == "planned" for action in result["actions"]))

    def test_conflict_prefers_newer_memory_timestamp(self):
        self.write_memory(
            "# MEMORY\n\n"
            "## Topic\n"
            "Body.\n"
        )
        self.set_mtime(self.memory_path, datetime(2026, 5, 5, 1, 0, 0))

        note = self.vault_path / "02_Knowledge" / "topic.md"
        note.write_text("# Topic\nOlder content\n", encoding="utf-8")
        self.set_mtime(note, datetime(2026, 5, 4, 12, 0, 0))

        result = sync_obsidian.sync_memory_and_vault(
            memory_path=self.memory_path,
            vault_path=self.vault_path,
            dry_run=False,
            log_dir=self.logs_path,
            now=datetime(2026, 5, 5, 1, 30, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(len(result["conflicts"]), 1)
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["winner"], "memory")
        updated = self.memory_path.read_text(encoding="utf-8")
        self.assertNotIn(sync_obsidian.STALE_PREFIX, updated)

    def test_check_links_finds_broken_wikilinks_and_markdown_links(self):
        topic = self.vault_path / "01_Projects" / "topic.md"
        topic.write_text(
            "# Topic\n"
            "Good wiki [[02_Knowledge/existing]]\n"
            "Bad wiki [[missing-note]]\n"
            "Bad md [missing](../02_Knowledge/missing.md)\n",
            encoding="utf-8",
        )
        existing = self.vault_path / "02_Knowledge" / "existing.md"
        existing.write_text("# Existing\n", encoding="utf-8")

        broken = sync_obsidian.check_links(self.vault_path)

        self.assertEqual(len(broken), 2)
        broken_set = {(item.link_type, item.target) for item in broken}
        self.assertIn(("wikilink", "missing-note"), broken_set)
        self.assertIn(("markdown", "../02_Knowledge/missing.md"), broken_set)

    def test_check_links_ignores_external_and_anchor_links(self):
        doc = self.vault_path / "memory" / "doc.md"
        doc.write_text(
            "# Doc\n"
            "[External](https://example.com)\n"
            "[Mail](mailto:test@example.com)\n"
            "[Anchor](#section)\n",
            encoding="utf-8",
        )

        broken = sync_obsidian.check_links(self.vault_path)
        self.assertEqual(broken, [])


if __name__ == "__main__":
    unittest.main()
