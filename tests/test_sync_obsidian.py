import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_obsidian.py"
SPEC = importlib.util.spec_from_file_location("sync_obsidian", MODULE_PATH)
sync_obsidian = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = sync_obsidian
SPEC.loader.exec_module(sync_obsidian)


class SyncObsidianTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.memory_path = self.root / "MEMORY.md"
        self.vault_path = self.root / "vault"
        self.log_dir = self.root / "logs"
        for directory in ("01_Projects", "02_Knowledge", "memory"):
            (self.vault_path / directory).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_sync(self, dry_run: bool = False) -> dict[str, object]:
        return sync_obsidian.sync(
            memory_path=self.memory_path,
            vault_path=self.vault_path,
            log_dir=self.log_dir,
            dry_run=dry_run,
        )

    def test_memory_section_creates_vault_reference(self) -> None:
        self.memory_path.write_text("# Workspace Memory\n\n## Project Alpha\nPlan details.\n", encoding="utf-8")

        report = self.run_sync()

        vault_file = self.vault_path / "memory" / "project-alpha.md"
        self.assertTrue(vault_file.exists())
        vault_text = vault_file.read_text(encoding="utf-8")
        self.assertIn("# Project Alpha", vault_text)
        self.assertIn("Source: [[MEMORY#Project Alpha]]", vault_text)

        memory_text = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("<!-- obsidian-sync:", memory_text)
        self.assertEqual(["vault_created"], [action["type"] for action in report["actions"]])

    def test_new_memory_vault_note_adds_daily_note_entry(self) -> None:
        self.memory_path.write_text("# Workspace Memory\n", encoding="utf-8")
        (self.vault_path / "memory" / "scratch-note.md").write_text("# Scratch Note\n\nFresh idea.\n", encoding="utf-8")

        report = self.run_sync()

        memory_text = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("## Daily Notes", memory_text)
        self.assertIn("added vault note memory/scratch-note.md (Scratch Note)", memory_text)
        self.assertEqual(1, len(report["notes_added"]))
        self.assertEqual("daily_note_added", report["notes_added"][0]["type"])

    def test_newer_vault_update_marks_memory_section_stale(self) -> None:
        self.memory_path.write_text("# Workspace Memory\n\n## Project Alpha\nPlan details.\n", encoding="utf-8")
        self.run_sync()

        vault_file = self.vault_path / "memory" / "project-alpha.md"
        vault_file.write_text(
            "\n".join(
                [
                    "<!-- obsidian-sync: {\"slug\": \"project-alpha\"} -->",
                    "# Project Alpha",
                    "",
                    "Source: [[MEMORY#Project Alpha]]",
                    "",
                    "## Synced Content",
                    "",
                    "Updated from vault.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        report = self.run_sync()

        memory_text = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("> Sync status: STALE - review memory/project-alpha.md", memory_text)
        self.assertEqual(["memory_marked_stale"], [action["type"] for action in report["actions"]])

    def test_dry_run_previews_without_writing_files(self) -> None:
        original_memory = "# Workspace Memory\n\n## Project Alpha\nPlan details.\n"
        self.memory_path.write_text(original_memory, encoding="utf-8")

        report = self.run_sync(dry_run=True)

        self.assertFalse((self.vault_path / "memory" / "project-alpha.md").exists())
        self.assertEqual(original_memory, self.memory_path.read_text(encoding="utf-8"))
        self.assertTrue(report["dry_run"])
        self.assertEqual(["vault_created"], [action["type"] for action in report["actions"]])
        report_path = Path(report["report_path"])
        self.assertTrue(report_path.exists())
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertTrue(payload["dry_run"])

    def test_conflict_prefers_newer_timestamp_and_logs_conflict(self) -> None:
        self.memory_path.write_text("# Workspace Memory\n\n## Project Alpha\nPlan details.\n", encoding="utf-8")
        self.run_sync()

        self.memory_path.write_text("# Workspace Memory\n\n## Project Alpha\nMemory changed.\n", encoding="utf-8")
        vault_file = self.vault_path / "memory" / "project-alpha.md"
        vault_file.write_text(
            "\n".join(
                [
                    "<!-- obsidian-sync: {\"slug\": \"project-alpha\"} -->",
                    "# Project Alpha",
                    "",
                    "Source: [[MEMORY#Project Alpha]]",
                    "",
                    "## Synced Content",
                    "",
                    "Vault changed.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        memory_timestamp = self.memory_path.stat().st_mtime
        vault_timestamp = memory_timestamp + 10
        sync_obsidian.os.utime(vault_file, (vault_timestamp, vault_timestamp))

        report = self.run_sync()

        memory_text = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("> Sync status: STALE - review memory/project-alpha.md", memory_text)
        self.assertEqual(1, len(report["conflicts"]))
        self.assertEqual("vault", report["conflicts"][0]["resolved_to"])


if __name__ == "__main__":
    unittest.main()
