"""Tests for scripts.auto_reflection."""

from __future__ import annotations

import importlib.util
import io
import json
import os
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
            insights = list(auto_reflection.read_and_extract(log_path, root, None))
        texts = [i.text.lower() for i in insights]
        self.assertTrue(any("traceback" in t for t in texts))

    def test_run_reflection_writes_learnings_and_skips_writes_on_dry_run(self):
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
                skip_openclaw=True,
            )
            self.assertFalse((root / ".learnings").exists())
            self.assertGreaterEqual(len(run_dry.insights), 1)

            run_full = auto_reflection.run_reflection(
                root,
                since_hours=1,
                extra_globs=[],
                dry_run=False,
                skip_openclaw=True,
            )
            self.assertGreater(run_full.files_scanned, 0)
            learnings = root / ".learnings"
            self.assertTrue(learnings.exists())
            self.assertTrue((learnings / "insights").exists())
            self.assertTrue((learnings / "summaries").exists())
            self.assertTrue((learnings / "auto_reflection.md").exists())
            ar_body = (learnings / "auto_reflection.md").read_text(encoding="utf-8")
            self.assertIn("What went well", ar_body)
            self.assertIn("What went wrong", ar_body)
            self.assertIn("Actionable insights", ar_body)
            md_files = list((learnings / "insights").glob("run_*.md"))
            self.assertEqual(len(md_files), 1)
            body = md_files[0].read_text(encoding="utf-8")
            self.assertIn("Lesson learned:", body)

            run_again = auto_reflection.run_reflection(
                root,
                since_hours=1,
                extra_globs=[],
                dry_run=False,
                skip_openclaw=True,
            )
            self.assertGreaterEqual(len(run_again.insights), 1)
            ar_again = (learnings / "auto_reflection.md").read_text(encoding="utf-8")
            self.assertIn("Repeated insights omitted", ar_again)

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

    def test_reflection_buckets_priority(self):
        wrong_ins = auto_reflection.Insight(
            text="Error: timeout connecting to API",
            source_paths=["logs/x.log"],
            severity="warning",
            category="integration",
        )
        well_ins = auto_reflection.Insight(
            text="Tests passed and build succeeded.",
            source_paths=["logs/y.log"],
            severity="info",
            category="testing",
        )
        act_ins = auto_reflection.Insight(
            text="Lesson learned: verify credentials before deploy.",
            source_paths=["memory/z.md"],
            severity="info",
            category="lesson",
        )
        well, wrong, action = auto_reflection.reflection_buckets([wrong_ins, well_ins, act_ins])
        self.assertEqual(len(wrong), 1)
        self.assertEqual(len(well), 1)
        self.assertEqual(len(action), 1)
        self.assertIn("timeout", wrong[0].text.lower())

    def test_openclaw_home_logs_included(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            ohome = Path(raw) / "fake_openclaw"
            (ohome / "logs").mkdir(parents=True)
            (ohome / "logs" / "session.log").write_text(
                "Error: simulated failure from OpenClaw home.\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"OPENCLAW_HOME": str(ohome)}, clear=False):
                run = auto_reflection.run_reflection(
                    root,
                    since_hours=48,
                    extra_globs=[],
                    dry_run=True,
                    skip_openclaw=False,
                )
            self.assertGreaterEqual(len(run.insights), 1)
            joined = " ".join(run.session_files)
            self.assertIn(".openclaw", joined)


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
                            "--skip-openclaw",
                        ]
                    )
            self.assertEqual(rc, 0)
            err = stderr.getvalue()
            self.assertIn("No REFLECTION_WEBHOOK_URL", err)
            self.assertIn("Reflection summary", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
