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
            source_paths=["memory/a.md"],
            severity="warning",
            category="integration",
        )
        b = auto_reflection.Insight(
            text="Error: timeout",
            source_paths=["memory/b.md"],
            severity="error",
            category="general",
        )
        out = auto_reflection.dedupe_insights([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].severity, "error")
        self.assertEqual(set(out[0].source_paths), {"memory/a.md", "memory/b.md"})

    def test_extract_from_json_walks_nested_errors(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            log_path = root / "memory" / "agent.json"
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
            insights = list(auto_reflection.read_and_extract(log_path, root))
        texts = [i.text.lower() for i in insights]
        self.assertTrue(any("traceback" in t for t in texts))

    def test_run_reflection_writes_auto_markdown_and_skips_writes_on_dry_run(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            mem = root / "memory" / "run_log.md"
            mem.parent.mkdir(parents=True)
            mem.write_text(
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
            self.assertGreaterEqual(len(run_dry.insights), 1)

            run_full = auto_reflection.run_reflection(
                root,
                since_hours=1,
                extra_globs=[],
                output_day=auto_reflection.utc_now(),
                dry_run=False,
            )
            self.assertGreater(run_full.files_scanned, 0)
            learnings = root / ".learnings"
            self.assertTrue(learnings.exists())
            auto_dir = learnings / "auto"
            self.assertTrue(auto_dir.is_dir())
            day = auto_reflection.utc_now().date().isoformat()
            out_md = auto_dir / f"{day}_reflection.md"
            self.assertTrue(out_md.exists())
            body = out_md.read_text(encoding="utf-8")
            self.assertIn("# Wins", body)
            self.assertIn("# Issues", body)
            self.assertIn("# Insights", body)
            self.assertIn("# Action Items", body)
            self.assertIn("Lesson learned:", body)
            latest = json.loads((learnings / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest.get("auto_reflection_md"), f".learnings/auto/{day}_reflection.md")

    def test_openclaw_session_json_extracts_wins_and_issues_sections(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            sess = root / "memory" / "sess1" / "session.json"
            sess.parent.mkdir(parents=True)
            sess.write_text(
                json.dumps(
                    {
                        "messages": [
                            {
                                "role": "assistant",
                                "content": "Decision: use PostgreSQL for the queue.",
                            },
                            {
                                "role": "assistant",
                                "content": "Traceback (most recent call last): connection refused",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            run = auto_reflection.run_reflection(
                root,
                since_hours=1,
                extra_globs=[],
                dry_run=False,
            )
            cats = {i.category for i in run.insights}
            self.assertIn("win", cats)
            self.assertIn("loss", cats)
            day = auto_reflection.utc_now().date().isoformat()
            body = (root / ".learnings" / "auto" / f"{day}_reflection.md").read_text(encoding="utf-8")
            self.assertIn("# Wins", body)
            self.assertIn("# Issues", body)

    def test_insights_dedupe_against_existing_learnings(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            lesson = "Lesson learned: verify API keys before deploying."
            prior = root / ".learnings" / "notes.md"
            prior.parent.mkdir(parents=True)
            prior.write_text(
                f"# Prior\n\n- {lesson}\n",
                encoding="utf-8",
            )
            mem = root / "memory" / "x.md"
            mem.parent.mkdir(parents=True)
            mem.write_text(
                lesson + "\n",
                encoding="utf-8",
            )
            auto_reflection.run_reflection(root, since_hours=1, dry_run=False)
            day = auto_reflection.utc_now().date().isoformat()
            body = (root / ".learnings" / "auto" / f"{day}_reflection.md").read_text(encoding="utf-8")
            insights_section = body.split("# Insights", 1)[1].split("# Action Items", 1)[0]
            self.assertNotIn("verify API keys before deploying", insights_section.lower())

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
            (root / "memory").mkdir(parents=True)
            (root / "memory" / "x.md").write_text("Error: something failed.\n")

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
                            "--stdout",
                        ]
                    )
            self.assertEqual(rc, 0)
            err = stderr.getvalue()
            self.assertIn("No REFLECTION_WEBHOOK_URL", err)
            self.assertIn("# Wins", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
