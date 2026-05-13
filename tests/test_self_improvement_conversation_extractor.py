"""Tests for scripts.self_improvement.conversation_extractor."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "self_improvement" / "conversation_extractor.py"
SPEC = importlib.util.spec_from_file_location("si_conversation_extractor", MODULE_PATH)
ce = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules.setdefault("si_conversation_extractor", ce)
SPEC.loader.exec_module(ce)


class ConversationExtractorTests(unittest.TestCase):
    def test_parse_sessions_list_json_variants(self):
        raw = json.dumps(
            {
                "sessions": [
                    {"id": "a1", "history_path": "h1.json"},
                    {"key": "b2", "path": "logs/b2.log"},
                ]
            }
        )
        rows = ce.parse_sessions_list(raw)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "a1")
        self.assertEqual(rows[0]["path"], "h1.json")

    def test_parse_sessions_list_plain_paths(self):
        rows = ce.parse_sessions_list("alpha.json\nbeta.log\n")
        self.assertEqual(rows[0]["path"], "alpha.json")
        self.assertEqual(rows[1]["id"], "beta")

    def test_extract_moments_tags_and_user_correction(self):
        segments = [
            (1, "assistant", "Try threads."),
            (2, "user", "Actually use asyncio instead."),
            (3, "assistant", "[error] Connection refused\nThen fixed."),
            (4, "assistant", "[insight]: Pool connections\n"),
        ]
        moments = ce.extract_moments_from_segments(segments, session_id="s", source="t.json")
        kinds = {m.kind for m in moments}
        self.assertIn("user_correction", kinds)
        self.assertIn("insight", kinds)
        self.assertIn("error", kinds)

    def test_extract_recent_writes_learnings(self):
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            hist = root / "sess.json"
            hist.write_text(
                json.dumps(
                    {
                        "messages": [
                            {"role": "user", "content": "Wrong approach."},
                            {"role": "user", "content": "Actually I need JSON output only."},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            lst = root / "sessions_list.json"
            lst.write_text(
                json.dumps({"sessions": [{"id": "sess", "path": "sess.json"}]}),
                encoding="utf-8",
            )
            argv = [
                "extract-recent",
                "--workspace",
                str(root),
                "--sessions-list",
                str(lst),
                "--limit",
                "2",
            ]
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = ce.main(argv)
            self.assertEqual(rc, 0)
            out_dir = root / ".learnings" / "conversation_patterns"
            self.assertTrue(out_dir.is_dir())
            mds = list(out_dir.glob("pattern_*.md"))
            self.assertGreaterEqual(len(mds), 1)

    def test_format_entry_stdout(self):
        import io
        import subprocess
        from contextlib import redirect_stdout

        payload = {
            "moment_kind": "correction",
            "summary": "Use pathlib",
            "detail": "Prefer Path over os.path.join.",
            "session_id": "x",
            "turn": 2,
        }
        repo = Path(__file__).resolve().parent.parent
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.self_improvement.conversation_extractor",
                "format-entry",
                "--stdin",
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=str(repo),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("moment_kind", proc.stdout)
        self.assertIn("Use pathlib", proc.stdout)

        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json", encoding="utf-8") as fh:
            json.dump(payload, fh)
            in_path = Path(fh.name)
        out_path = in_path.parent / "out_entry_fmt.md"
        try:
            parser = ce.build_parser()
            ns = parser.parse_args(["format-entry", "--input", str(in_path), "-o", str(out_path)])
            buf2 = io.StringIO()
            with redirect_stdout(buf2):
                rc = ce.cmd_format_entry(ns)
            self.assertEqual(rc, 0)
            self.assertTrue(out_path.exists())
            body = out_path.read_text(encoding="utf-8")
            self.assertIn("pathlib", body)
        finally:
            in_path.unlink(missing_ok=True)  # type: ignore[arg-type]
            out_path.unlink(missing_ok=True)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
