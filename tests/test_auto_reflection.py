"""Tests for scripts.auto_reflection."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from collections import Counter
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
            insights = list(auto_reflection.read_and_extract(log_path, root))
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
            )
            self.assertFalse((root / ".learnings").exists())
            self.assertGreaterEqual(len(run_dry.insights), 1)
            self.assertTrue(run_dry.session_clusters)
            self.assertTrue(run_dry.key_insights)
            self.assertTrue(run_dry.trend_notes)

            run_full = auto_reflection.run_reflection(
                root,
                since_hours=1,
                extra_globs=[],
                dry_run=False,
            )
            self.assertGreater(run_full.files_scanned, 0)
            learnings = root / ".learnings"
            self.assertTrue(learnings.exists())
            self.assertTrue((learnings / "insights").exists())
            self.assertTrue((learnings / "summaries").exists())
            self.assertTrue((learnings / "reports").exists())
            md_files = list((learnings / "insights").glob("run_*.md"))
            self.assertEqual(len(md_files), 1)
            body = md_files[0].read_text(encoding="utf-8")
            self.assertIn("Lesson learned:", body)
            self.assertIn("## Session analysis", body)
            self.assertIn("## Key insights", body)
            self.assertIn("## Trends", body)
            rep = list((learnings / "reports").glob("reflection_*.json"))
            self.assertEqual(len(rep), 1)
            payload = json.loads(rep[0].read_text(encoding="utf-8"))
            self.assertEqual(payload.get("schema_version"), auto_reflection.SCHEMA_VERSION)
            self.assertIn("session_clusters", payload["run"])
            mem = root / "memory" / "auto_reflection_log.md"
            self.assertTrue(mem.exists())
            self.assertIn(run_full.run_id, mem.read_text(encoding="utf-8"))

    def test_cluster_key_for_rel_groups_logs_subdirs(self):
        self.assertEqual(auto_reflection.cluster_key_for_rel("logs/agents/foo.log"), "logs/agents")
        self.assertEqual(auto_reflection.cluster_key_for_rel("memory/x.md"), "memory")

    def test_detect_trends_recurring_fingerprints(self):
        fp = auto_reflection.insight_fingerprint("Error: timeout contacting service")
        prior = [
            auto_reflection._PriorRunSnap(
                run_id="a",
                fingerprints={fp},
                category_counts=Counter({"integration": 1}),
                insight_count=1,
            ),
            auto_reflection._PriorRunSnap(
                run_id="b",
                fingerprints={fp},
                category_counts=Counter({"integration": 1}),
                insight_count=1,
            ),
        ]
        cur = [
            auto_reflection.Insight(
                text="Error: timeout contacting service",
                source_paths=["logs/x/a.log"],
                severity="warning",
                category="integration",
            )
        ]
        notes = auto_reflection.detect_trends(cur, prior)
        self.assertTrue(any("recur" in n.lower() for n in notes))

    def test_pick_key_insights_prefers_lessons_and_errors(self):
        insights = [
            auto_reflection.Insight(
                text="minor note about formatting",
                source_paths=["logs/a.log"],
                severity="info",
                category="general",
            ),
            auto_reflection.Insight(
                text="Lesson learned: always run tests",
                source_paths=["logs/b.log"],
                severity="info",
                category="lesson",
            ),
            auto_reflection.Insight(
                text="Error: build failed",
                source_paths=["logs/c.log"],
                severity="error",
                category="general",
            ),
        ]
        picked = auto_reflection.pick_key_insights(insights, limit=2)
        texts = [p.text for p in picked]
        self.assertIn("Lesson learned: always run tests", texts)
        self.assertIn("Error: build failed", texts)

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
            self.assertIn("Reflection summary", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
