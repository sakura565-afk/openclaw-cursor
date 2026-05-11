import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.dreams.dream_tracker import DreamTracker


class DreamTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_ws = os.environ.get("OPENCLAW_WORKSPACE")
        self._ws_home = tempfile.TemporaryDirectory()
        self.addCleanup(self._ws_home.cleanup)
        os.environ["OPENCLAW_WORKSPACE"] = str(Path(self._ws_home.name) / "oc_ws")

    def tearDown(self) -> None:
        if self._prev_ws is None:
            os.environ.pop("OPENCLAW_WORKSPACE", None)
        else:
            os.environ["OPENCLAW_WORKSPACE"] = self._prev_ws

    def make_repo(self):
        temp_dir = tempfile.TemporaryDirectory()
        repo_root = Path(temp_dir.name)
        (repo_root / "README.md").write_text(
            "# OpenClaw\n\nWe should improve observability for dream work.\n",
            encoding="utf-8",
        )
        (repo_root / "conversation.md").write_text(
            "Need to build a dream sync panel for operators.\n",
            encoding="utf-8",
        )
        (repo_root / "notes.py").write_text(
            "# TODO: add a scheduler for nightly orchestration health checks\n",
            encoding="utf-8",
        )
        return temp_dir, repo_root

    def test_create_dream_builds_index_and_markdown(self):
        temp_dir, repo_root = self.make_repo()
        self.addCleanup(temp_dir.cleanup)
        tracker = DreamTracker(repo_root)

        dream = tracker.create_dream("Polish dream tracker")

        self.assertEqual(dream["status"], "idea")
        self.assertTrue((repo_root / "dreams" / "dream_index.json").exists())
        self.assertTrue((repo_root / dream["path"]).exists())
        markdown = (repo_root / dream["path"]).read_text(encoding="utf-8")
        self.assertIn("# Polish dream tracker", markdown)
        self.assertIn("## History", markdown)

    def test_auto_generation_uses_system_and_conversation_patterns(self):
        temp_dir, repo_root = self.make_repo()
        self.addCleanup(temp_dir.cleanup)
        tracker = DreamTracker(repo_root)

        dreams = tracker.list_dreams()

        titles = {dream["title"] for dream in dreams}
        sources = {dream["source"] for dream in dreams}
        self.assertIn("Dream: Richer OpenClaw orchestration visibility", titles)
        self.assertIn("Dream: Resolve note from notes", titles)
        self.assertIn("Dream: A dream sync panel for operators", titles)
        self.assertIn("auto-system-pattern", sources)
        self.assertIn("auto-conversation-analysis", sources)
        self.assertNotIn("Dream: Resolve note from dream_tracker", titles)
        self.assertNotIn("Dream: Resolve note from test_dream_tracker", titles)

    def test_research_and_implement_move_dream_through_workflow(self):
        temp_dir, repo_root = self.make_repo()
        self.addCleanup(temp_dir.cleanup)
        tracker = DreamTracker(repo_root)
        dream = tracker.create_dream("Ship dream automation")

        planning = tracker.research_dream(dream["id"])
        self.assertEqual(planning["status"], "planning")
        self.assertTrue((repo_root / "dreams" / "active" / f"{dream['id']}.md").exists())

        implementing = tracker.implement_dream(dream["id"])
        self.assertEqual(implementing["status"], "implementing")

        done = tracker.implement_dream(dream["id"])
        self.assertEqual(done["status"], "done")
        self.assertTrue(
            (repo_root / "dreams" / "implemented" / f"{dream['id']}.md").exists()
        )
        self.assertFalse(
            (repo_root / "dreams" / "active" / f"{dream['id']}.md").exists()
        )

        index = json.loads((repo_root / "dreams" / "dream_index.json").read_text())
        self.assertEqual(index["dreams"][dream["id"]]["status"], "done")
        self.assertEqual(
            index["dreams"][dream["id"]]["path"],
            f"dreams/implemented/{dream['id']}.md",
        )

    def test_cli_create_and_status_commands_work(self):
        temp_dir, repo_root = self.make_repo()
        self.addCleanup(temp_dir.cleanup)

        env = dict(os.environ)
        env["PYTHONPATH"] = "/workspace"

        create_result = subprocess.run(
            [sys.executable, "-m", "src.dreams.dream_tracker", "create", "CLI dream"],
            cwd=repo_root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(create_result.returncode, 0, create_result.stderr)
        dream_id = create_result.stdout.split("|", 1)[0].strip()

        status_result = subprocess.run(
            [sys.executable, "-m", "src.dreams.dream_tracker", "status", dream_id],
            cwd=repo_root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(status_result.returncode, 0, status_result.stderr)
        payload = json.loads(status_result.stdout)
        self.assertEqual(payload["id"], dream_id)
        self.assertEqual(payload["title"], "CLI dream")


if __name__ == "__main__":
    unittest.main()
