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
                        ],
                    },
                ),
                encoding="utf-8",
            )
            errors: list[str] = []
            insights = list(auto_reflection.read_and_extract(log_path, root, errors))
        texts = [i.text.lower() for i in insights]
        self.assertTrue(any("traceback" in t for t in texts))

    def test_run_reflection_writes_memory_learnings_and_skips_writes_on_dry_run(self):
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
                days=1 / 24.0,
                extra_globs=[],
                dry_run=True,
            )
            self.assertFalse((root / "memory" / ".learnings").exists())
            self.assertGreaterEqual(len(run_dry.insights), 1)

            run_full = auto_reflection.run_reflection(
                root,
                days=1 / 24.0,
                extra_globs=[],
                dry_run=False,
            )
            self.assertGreater(run_full.sessions_processed, 0)
            learnings = root / "memory" / ".learnings"
            self.assertTrue(learnings.exists())
            daily = root / run_full.daily_markdown_path
            self.assertTrue(daily.exists())
            refl = daily.read_text(encoding="utf-8")
            self.assertIn("## Metrics", refl)
            self.assertIn("Task completion rate", refl)
            self.assertIn("Tool success rate", refl)
            self.assertIn("Context switches", refl)
            latest = json.loads((learnings / "latest.json").read_text(encoding="utf-8"))
            self.assertIn("daily_markdown", latest)
            self.assertTrue(str(latest["daily_markdown"]).endswith(".md"))

    def test_openclaw_session_json_extracts_wins_losses_in_daily_md(self):
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
                        ],
                    },
                ),
                encoding="utf-8",
            )

            run = auto_reflection.run_reflection(
                root,
                days=1 / 24.0,
                extra_globs=[],
                dry_run=False,
            )
            cats = {i.category for i in run.insights}
            self.assertIn("win", cats)
            self.assertIn("loss", cats)
            daily = root / run.daily_markdown_path
            body = daily.read_text(encoding="utf-8")
            self.assertIn("## Win highlights", body)
            self.assertIn("## Loss / risk highlights", body)

    def test_agents_md_learnings_section_merged_into_daily(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "logs").mkdir(parents=True)
            (root / "logs" / "run.log").write_text("Fixed: tests now pass.\n", encoding="utf-8")
            (root / "AGENTS.md").write_text(
                "# AGENTS\n\n## .learnings / memory\n\n- Prefer small PRs.\n- Run linters before push.\n",
                encoding="utf-8",
            )
            run_ag = auto_reflection.run_reflection(root, days=1 / 24.0, extra_globs=[], dry_run=False)
            daily = root / run_ag.daily_markdown_path
            text = daily.read_text(encoding="utf-8")
            self.assertIn("## Guidance from AGENTS.md", text)
            self.assertIn("Prefer small PRs.", text)

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
    def test_main_emits_machine_line_and_stderr_posts_log_without_network(self):
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
                            "run",
                            "--root",
                            str(root),
                            "--days",
                            "1",
                            "--stdout-summary",
                        ],
                    )
            self.assertEqual(rc, 0)
            err = stderr.getvalue()
            self.assertIn("No REFLECTION_WEBHOOK_URL", err)
            out = stdout.getvalue()
            self.assertIn(auto_reflection.MACHINE_PREFIX, out)
            self.assertIn("sessions_processed", out)
            self.assertIn("Reflection summary", out)

    def test_legacy_argv_maps_since_hours_to_days(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "logs").mkdir(parents=True)
            (root / "logs" / "x.log").write_text("Error: x.\n")
            stdout = io.StringIO()
            with mock.patch.object(auto_reflection.sys, "stdout", stdout):
                rc = auto_reflection.main(
                    ["--root", str(root), "--since-hours", "24", "--dry-run"],
                )
            self.assertEqual(rc, 0)
            line = [ln for ln in stdout.getvalue().splitlines() if ln.startswith(auto_reflection.MACHINE_PREFIX)][0]
            payload = json.loads(line.split(" ", 1)[1])
            self.assertTrue(payload.get("dry_run"))


if __name__ == "__main__":
    unittest.main()
