"""Tests for scripts.auto_reflection."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "auto_reflection.py"
SPEC = importlib.util.spec_from_file_location("auto_reflection", MODULE_PATH)
auto_reflection = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules.setdefault("auto_reflection", auto_reflection)
SPEC.loader.exec_module(auto_reflection)


def _sample_session_messages() -> dict:
    return {
        "messages": [
            {"role": "user", "content": "Diagnose the OpenClaw session timeout in production."},
            {
                "role": "assistant",
                "content": "I'll search logs and run tests.",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "content": '{"status": 500, "error": "upstream failure"}'},
            {
                "role": "assistant",
                "content": "Traceback (most recent call last): simulated connector failure",
            },
        ]
    }


class AutoReflectionStatsTests(unittest.TestCase):
    def test_build_daily_markdown_has_required_sections(self):
        stats = auto_reflection.SessionStats()
        md = auto_reflection.build_daily_markdown(
            auto_reflection.utc_now(),
            stats,
            api_used=False,
            days_window=3,
        )
        for heading in ("## Wins", "## Issues", "## Insights", "## Next Steps"):
            self.assertIn(heading, md)

    def test_ingest_counts_tools_and_messages(self):
        stats = auto_reflection.SessionStats()
        segs = auto_reflection.segments_from_json_data(_sample_session_messages())
        auto_reflection.ingest_segments(stats, segs, "test:unit")
        self.assertEqual(stats.sessions_count, 1)
        self.assertGreaterEqual(stats.tool_invocations, 1)
        self.assertGreaterEqual(stats.user_messages, 1)
        self.assertGreaterEqual(stats.assistant_messages, 1)
        self.assertGreater(stats.error_like_hits, 0)
        self.assertGreater(stats.failed_tool_signals, 0)
        topics = stats.topics_covered(20)
        words = {w for w, _ in topics}
        self.assertTrue(words & {"openclaw", "diagnose", "session", "timeout", "production"})


class AutoReflectionRunTests(unittest.TestCase):
    def test_run_reflection_writes_dated_markdown(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "logs").mkdir(parents=True)
            path = root / "logs" / "agent.json"
            path.write_text(json.dumps(_sample_session_messages()), encoding="utf-8")

            run = auto_reflection.run_reflection(root, days=7, extra_globs=[], dry_run=False)
            day = auto_reflection.utc_now().date().isoformat()
            out = root / ".learnings" / f"{day}.md"
            self.assertTrue(out.exists())
            body = out.read_text(encoding="utf-8")
            self.assertIn("## Wins", body)
            self.assertIn("read_file", body)
            self.assertEqual(run.daily_markdown, body)

    def test_dry_run_skips_learnings_dir(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "logs").mkdir(parents=True)
            (root / "logs" / "a.json").write_text(
                json.dumps(_sample_session_messages()),
                encoding="utf-8",
            )
            auto_reflection.run_reflection(root, days=1, dry_run=True)
            self.assertFalse((root / ".learnings").exists())

    def test_days_window_excludes_stale_files_by_mtime(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "logs").mkdir(parents=True)
            old = root / "logs" / "stale.json"
            old.write_text(json.dumps(_sample_session_messages()), encoding="utf-8")
            ancient = time.time() - 40 * 24 * 3600
            os.utime(old, (ancient, ancient))

            fresh = root / "logs" / "fresh.json"
            fresh.write_text(json.dumps(_sample_session_messages()), encoding="utf-8")

            run = auto_reflection.run_reflection(root, days=7, dry_run=True)
            self.assertEqual(run.stats.sessions_count, 1)
            self.assertIn("fresh.json", "".join(run.sources_used))


class AutoReflectionApiTests(unittest.TestCase):
    def test_fetch_sessions_history_parses_payload(self):
        payload = json.dumps(
            {
                "sessions": [
                    {"id": "s1", "transcript": _sample_session_messages()},
                ]
            }
        ).encode("utf-8")

        class DummyResp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return payload

        def fake_urlopen(req, timeout=30):
            self.assertIn("days=2", req.full_url)
            return DummyResp()

        with mock.patch.dict(
            os.environ,
            {"OPENCLAW_SESSIONS_HISTORY_URL": "https://example.invalid/history"},
            clear=False,
        ):
            with mock.patch.object(auto_reflection.urllib.request, "urlopen", fake_urlopen):
                pairs = auto_reflection.fetch_sessions_via_history_api(2)

        self.assertEqual(len(pairs), 1)
        label, segs = pairs[0]
        self.assertTrue(label.startswith("api:"))
        self.assertGreater(len(segs), 0)

    def test_run_reflection_merges_api_and_disk(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "logs").mkdir(parents=True)
            (root / "logs" / "disk.json").write_text(json.dumps(_sample_session_messages()), encoding="utf-8")

            api_body = json.dumps(
                {"sessions": [{"id": "remote", "messages": _sample_session_messages()["messages"]}]}
            ).encode("utf-8")

            class DummyResp:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self):
                    return api_body

            with mock.patch.dict(
                os.environ,
                {"OPENCLAW_SESSIONS_HISTORY_URL": "https://example.invalid/x?token=1"},
                clear=False,
            ):
                with mock.patch.object(auto_reflection.urllib.request, "urlopen", lambda *a, **k: DummyResp()):
                    run = auto_reflection.run_reflection(root, days=1, dry_run=True)

            self.assertGreaterEqual(run.stats.sessions_count, 2)
            self.assertTrue(any(s.startswith("api:") for s in run.sources_used))


class AutoReflectionWebhookTests(unittest.TestCase):
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
    def test_main_prints_summary_and_stderr_meta(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "logs").mkdir(parents=True)
            (root / "logs" / "x.json").write_text(json.dumps(_sample_session_messages()), encoding="utf-8")

            stderr = io.StringIO()
            stdout = io.StringIO()
            with mock.patch.object(auto_reflection.sys, "stderr", stderr):
                with mock.patch.object(auto_reflection.sys, "stdout", stdout):
                    rc = auto_reflection.main(
                        [
                            "--root",
                            str(root),
                            "--days",
                            "1",
                            "--stdout-summary",
                        ]
                    )
            self.assertEqual(rc, 0)
            err = stderr.getvalue()
            self.assertIn("No REFLECTION_WEBHOOK_URL", err)
            out = stdout.getvalue()
            self.assertIn("## Wins", out)
            self.assertIn("OpenClaw self-reflection", out)

    def test_main_dry_run_prints_markdown_to_stdout(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "logs").mkdir(parents=True)
            (root / "logs" / "y.json").write_text(json.dumps(_sample_session_messages()), encoding="utf-8")

            stderr = io.StringIO()
            stdout = io.StringIO()
            with mock.patch.object(auto_reflection.sys, "stderr", stderr):
                with mock.patch.object(auto_reflection.sys, "stdout", stdout):
                    rc = auto_reflection.main(
                        ["--root", str(root), "--days", "1", "--dry-run"]
                    )
            self.assertEqual(rc, 0)
            self.assertFalse((root / ".learnings").exists())
            self.assertIn("## Next Steps", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
