import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.skills.proactive_watcher import ProactiveSkillWatcher


class ProactiveSkillWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.openclaw_home = self.root / ".openclaw"
        self.skill_root = self.openclaw_home / "skills"
        self.workspace_skill_root = self.openclaw_home / "workspace" / "skills"
        self.log_root = self.openclaw_home / "logs"
        self.workspace_log_root = self.openclaw_home / "workspace" / "logs"
        self.report_dir = self.root / "skills"
        for path in (
            self.skill_root,
            self.workspace_skill_root,
            self.log_root,
            self.workspace_log_root,
            self.report_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        alpha_dir = self.skill_root / "alpha"
        (alpha_dir / "tests").mkdir(parents=True, exist_ok=True)
        (alpha_dir / "README.md").write_text("Alpha docs\n", encoding="utf-8")
        (alpha_dir / "run.py").write_text("print('alpha')\n", encoding="utf-8")
        (alpha_dir / "tests" / "test_alpha.py").write_text(
            "def test_alpha():\n    assert True\n",
            encoding="utf-8",
        )

        beta_dir = self.workspace_skill_root / "beta"
        beta_dir.mkdir(parents=True, exist_ok=True)
        (beta_dir / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")

        old_mtime = (datetime.now(timezone.utc) - timedelta(days=45)).timestamp()
        os.utime(beta_dir / "broken.py", (old_mtime, old_mtime))

        self.log_root.joinpath("activity.log").write_text(
            textwrap.dedent(
                """
                2026-05-04T10:00:00 alpha executed successfully
                2026-05-04T10:10:00 alpha finished run
                2026-05-04T10:20:00 alpha completed task
                2026-05-04T10:30:00 beta ERROR failed to import dependency 123
                2026-05-04T10:31:00 beta ERROR failed to import dependency 456
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        self.watcher = ProactiveSkillWatcher(
            skill_roots=[self.skill_root, self.workspace_skill_root],
            log_roots=[self.log_root, self.workspace_log_root],
            report_dir=self.report_dir,
            now=datetime(2026, 5, 4, tzinfo=timezone.utc),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_scan_discovers_skills_and_tracks_files(self) -> None:
        skills = self.watcher.scan_skills()

        self.assertEqual(set(skills), {"alpha", "beta"})
        self.assertTrue(skills["alpha"].has_docs)
        self.assertTrue(skills["alpha"].has_tests)
        self.assertFalse(skills["beta"].has_docs)
        self.assertFalse(skills["beta"].has_tests)
        self.assertTrue(skills["beta"].syntax_failures)

    def test_usage_and_error_analysis_classify_skills(self) -> None:
        self.watcher.scan_skills()

        usage = self.watcher.analyze_usage()
        errors = self.watcher.analyze_errors()

        self.assertEqual(usage["alpha"].usage_label, "frequent")
        self.assertEqual(usage["alpha"].usage_count, 3)
        self.assertEqual(usage["beta"].usage_label, "rare")
        self.assertEqual(usage["beta"].usage_count, 2)
        self.assertEqual(list(errors["beta"].error_patterns.values()), [2])
        self.assertIn("failed to import dependency #", next(iter(errors["beta"].error_patterns)))

    def test_suggestions_and_report_generation(self) -> None:
        self.watcher.scan_skills()
        suggestions = self.watcher.build_suggestions()

        self.assertEqual(suggestions["alpha"].suggestions, [])
        self.assertIn("Update docs", suggestions["beta"].suggestions[0])
        self.assertTrue(any("Fix broken scripts" in item for item in suggestions["beta"].suggestions))
        self.assertTrue(any("Add missing tests" in item for item in suggestions["beta"].suggestions))

        report_path = self.watcher.write_report()
        report_text = report_path.read_text(encoding="utf-8")

        self.assertTrue(report_path.exists())
        self.assertIn("# OpenClaw Skill Health Report (2026-05-04)", report_text)
        self.assertIn("**beta**", report_text)
        self.assertIn("Fix broken scripts", report_text)

    def test_cli_scan_writes_report(self) -> None:
        env = os.environ.copy()
        env["OPENCLAW_HOME"] = str(self.openclaw_home)
        env["OPENCLAW_REPORT_DIR"] = str(self.report_dir)
        existing_pythonpath = env.get("PYTHONPATH")
        repo_root = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = (
            repo_root if not existing_pythonpath else os.pathsep.join([repo_root, existing_pythonpath])
        )

        result = subprocess.run(
            [sys.executable, "-m", "src.skills.proactive_watcher", "scan"],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Discovered 2 skills", result.stdout)
        report_path = self.report_dir / "health_report_20260504.md"
        self.assertTrue(report_path.exists())


if __name__ == "__main__":
    unittest.main()
