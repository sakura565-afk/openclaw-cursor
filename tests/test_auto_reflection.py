"""Tests for scripts.auto_reflection."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "auto_reflection.py"
SPEC = importlib.util.spec_from_file_location("auto_reflection", MODULE_PATH)
auto_reflection = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules.setdefault("auto_reflection", auto_reflection)
SPEC.loader.exec_module(auto_reflection)


class AutoReflectionTests(unittest.TestCase):
    def test_dedupe_insights_collapses_near_duplicates(self):
        a = auto_reflection.Insight(
            text="Error: timeout",
            source_paths=["logs/a.log"],
            severity="warning",
            category="integration",
        )
        b = auto_reflection.Insight(
            text="Error: timeout",
            source_paths=["logs/b.log"],
            severity="error",
            category="general",
        )
        out = auto_reflection.dedupe_insights([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].severity, "error")
        self.assertEqual(set(out[0].source_paths), {"logs/a.log", "logs/b.log"})

    def test_extract_from_json_walks_nested_errors(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            log_path = root / "logs" / "agent.json"
            log_path.parent.mkdir(parents=True)
            log_path.write_text(
                json.dumps(
                    {
                        "turns": [
                            {"detail": "Traceback (most recent call last): simulated"},
                            {"message": "all good"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            insights = list(auto_reflection.read_and_extract(log_path, "logs/agent.json"))
        texts = [i.text.lower() for i in insights]
        self.assertTrue(any("traceback" in t for t in texts))

    def test_run_reflection_writes_daily_and_skips_writes_on_dry_run(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "logs").mkdir(parents=True)
            (root / "logs" / "run.log").write_text(
                "Lesson learned: verify API keys before deploying.\n"
                "Fatal: database migration failed.\n",
                encoding="utf-8",
            )

            run_dry = auto_reflection.run_reflection(
                root,
                since_hours=1,
                extra_globs=[],
                dry_run=True,
            )
            self.assertFalse((root / ".learnings").exists())
            self.assertGreaterEqual(len(run_dry.went_wrong), 1)

            run_full = auto_reflection.run_reflection(
                root,
                since_hours=1,
                extra_globs=[],
                dry_run=False,
            )
            self.assertGreater(run_full.files_scanned, 0)
            learnings = root / ".learnings"
            self.assertTrue(learnings.exists())
            daily_dir = learnings / "daily"
            self.assertTrue(daily_dir.exists())
            md_files = list(daily_dir.glob("*.md"))
            self.assertEqual(len(md_files), 1)
            body = md_files[0].read_text(encoding="utf-8")
            self.assertIn("Lesson learned:", body)
            self.assertIn("What went well", body)
            self.assertIn("What went wrong", body)

    def test_weekly_distill_appends_monthly(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            daily_dir = root / ".learnings" / "daily"
            daily_dir.mkdir(parents=True)
            day = auto_reflection.utc_now().replace(hour=12, minute=0, second=0, microsecond=0)
            p = auto_reflection.daily_reflection_path(root, day)
            p.write_text(
                "## What went well\n\n- Task completed successfully.\n\n"
                "## What went wrong\n\n- Timeout connecting to API.\n\n"
                "## Actionable insights\n\n- Add retries with backoff.\n",
                encoding="utf-8",
            )
            mp = auto_reflection.run_weekly_distill(root, end=day, dry_run=False)
            self.assertIsNotNone(mp)
            assert mp is not None
            text = mp.read_text(encoding="utf-8")
            self.assertIn("Monthly insights", text)
            self.assertIn("Timeout connecting", text)

    def test_post_webhook_uses_json_post(self):
        captured: dict[str, object] = {}

        class DummyResp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"ok":true}'

        def fake_urlopen(req, timeout=30):
            captured["data"] = json.loads(req.data.decode())
            captured["hdr"] = req.get_header("Content-type")
            return DummyResp()

        with mock.patch.object(auto_reflection.urllib.request, "urlopen", fake_urlopen):
            ok, msg = auto_reflection.post_webhook(
                "https://hooks.example.invalid/x",
                {"text": "hi", "meta": {"k": 1}},
            )

        self.assertTrue(ok)
        self.assertEqual(captured["data"]["text"], "hi")
        self.assertEqual(captured["data"]["meta"]["k"], 1)


class AutoReflectionMainTests(unittest.TestCase):
    def test_main_stderr_posts_log_without_network(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "logs").mkdir(parents=True)
            (root / "logs" / "x.log").write_text("Error: something failed.\n")

            stderr = io.StringIO()
            stdout = io.StringIO()
            with mock.patch.object(auto_reflection.sys, "stderr", stderr):
                with mock.patch.object(auto_reflection.sys, "stdout", stdout):
                    rc = auto_reflection.main(
                        [
                            "--root",
                            str(root),
                            "--since-hours",
                            "24",
                            "--stdout-summary",
                        ]
                    )
            self.assertEqual(rc, 0)
            err = stderr.getvalue()
            self.assertIn("No REFLECTION_WEBHOOK_URL", err)
            self.assertIn("Daily reflection", stdout.getvalue())

    def test_main_view_lists_recent(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            daily = root / ".learnings" / "daily"
            daily.mkdir(parents=True)
            (daily / "2026-05-10.md").write_text("# x\n", encoding="utf-8")

            stderr = io.StringIO()
            stdout = io.StringIO()
            with mock.patch.object(auto_reflection.sys, "stderr", stderr):
                with mock.patch.object(auto_reflection.sys, "stdout", stdout):
                    rc = auto_reflection.main(["--root", str(root), "--view"])
            self.assertEqual(rc, 0)
            self.assertIn("2026-05-10.md", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
