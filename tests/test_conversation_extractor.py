import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "conversation_extractor.py"
SPEC = importlib.util.spec_from_file_location("conversation_extractor", MODULE_PATH)
conversation_extractor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["conversation_extractor"] = conversation_extractor
SPEC.loader.exec_module(conversation_extractor)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "openclaw_session_sample.json"


REPO_ROOT = Path(__file__).resolve().parent.parent


class ConversationExtractorTests(unittest.TestCase):
    def test_parse_sample_json_turns_and_tools(self):
        segs = conversation_extractor.parse_session_log(FIXTURE)
        self.assertTrue(len(segs) >= 4)
        kinds = [conversation_extractor.classify_kind(r, t) for _, r, t in segs]
        self.assertIn("user", kinds)
        self.assertIn("agent", kinds)
        self.assertIn("tool_invocation", kinds)

    def test_extract_writes_json_index_and_dedup(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "conv_out"
            sample = root / "session.json"
            sample.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

            status, outp = conversation_extractor.extract_session_to_learnings(
                sample, root, out, force=False
            )
            self.assertEqual(status, "written")
            assert outp is not None
            self.assertTrue(outp.name.endswith(conversation_extractor.SESSION_JSON_SUFFIX))

            data = json.loads(outp.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"], 1)
            self.assertTrue(data["content_fingerprint"])
            self.assertTrue(data["turns"])
            self.assertTrue(data["conversations"])
            conv0 = data["conversations"][0]
            self.assertIn("topic", conv0)
            self.assertIn("intent", conv0)
            self.assertIn("outcome", conv0)
            self.assertIn("sentiment_hints", conv0)
            self.assertIn("tool_dependencies", conv0)
            self.assertIn("read_file", conv0.get("tool_dependencies", []))

            idx = out / "index.md"
            self.assertTrue(idx.exists())
            self.assertIn("read_file", idx.read_text(encoding="utf-8"))

            status2, outp2 = conversation_extractor.extract_session_to_learnings(
                sample, root, out, force=False
            )
            self.assertEqual(status2, "duplicate")
            self.assertIsNone(outp2)

            hits = conversation_extractor.search_records(out, keyword="widget", outcome="mixed")
            self.assertTrue(any("widget" in h["conversation"]["topic"].lower() for h in hits))

    def test_cli_extract_and_search(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "out"
            sample = root / "sess.json"
            sample.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

            cmd = [
                sys.executable,
                str(MODULE_PATH),
                "extract",
                "--workspace-root",
                str(root),
                "--output-dir",
                str(out),
                str(sample),
            ]
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
                env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
            )
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

            cmd2 = [
                sys.executable,
                str(MODULE_PATH),
                "search",
                "--workspace-root",
                str(root),
                "--output-dir",
                str(out),
                "--keyword",
                "CI",
                "--json",
            ]
            r2 = subprocess.run(
                cmd2,
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
                env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
            )
            self.assertEqual(r2.returncode, 0, r2.stderr)
            lines = [ln for ln in r2.stdout.splitlines() if ln.strip()]
            self.assertTrue(lines)
            row = json.loads(lines[0])
            self.assertIn("conversation", row)


if __name__ == "__main__":
    unittest.main()
