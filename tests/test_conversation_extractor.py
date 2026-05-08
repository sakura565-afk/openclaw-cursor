from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import conversation_extractor as ce  # noqa: E402


class ConversationExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_parse_tool_use_and_tool_result(self) -> None:
        payload = {
            "messages": [
                {"role": "user", "content": "Decision: ship the hotfix today."},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "read_file", "id": "1"},
                        {
                            "type": "tool_result",
                            "content": "Error: ENOENT: missing file\n",
                        },
                    ],
                },
                {"role": "assistant", "content": "Learning: verify the path before reading."},
            ]
        }
        p = self.root / "sess.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        segs = ce.parse_session_log(p)
        roles = [r for _, r, _ in segs]
        self.assertIn("tool", roles)
        self.assertIn("tool_result", roles)
        digest = ce.analyze_segments(segs, p.as_posix(), self.root)
        self.assertTrue(any("hotfix" in d for d in digest.decisions))
        self.assertTrue(any("Learning" in x or "verify" in x for x in digest.learnings))
        self.assertTrue(any("ENOENT" in e or "Error" in e for e in digest.errors))

    def test_dedupe_preserve_order(self) -> None:
        items = ["alpha", "alpha", "  alpha  ", "beta", "alpha with more"]
        out = ce.dedupe_preserve_order(items)
        self.assertEqual(out[0], "alpha")
        self.assertIn("beta", out)

    def test_cli_input_output_json(self) -> None:
        payload = {"messages": [{"role": "user", "content": "Outcome: tests passed."}]}
        src = self.root / "in.json"
        src.write_text(json.dumps(payload), encoding="utf-8")
        dst = self.root / "out.json"
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            code = ce.main(
                [
                    "--input",
                    str(src),
                    "--output",
                    str(dst),
                    "--workspace",
                    str(self.root),
                    "--workspace-root",
                    str(self.root),
                ]
            )
        self.assertEqual(code, 0)
        self.assertTrue(dst.is_file())
        data = json.loads(dst.read_text(encoding="utf-8"))
        self.assertIn("taxonomy", data)
        self.assertIn("key_outcomes", data)
        self.assertEqual(data["source"], src.name)

    def test_cli_batch_explicit(self) -> None:
        a = self.root / "a.json"
        b = self.root / "b.json"
        a.write_text(json.dumps({"messages": [{"role": "user", "content": "hello"}]}), encoding="utf-8")
        b.write_text(json.dumps({"messages": [{"role": "user", "content": "world"}]}), encoding="utf-8")
        outd = self.root / "batch_out"
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            code = ce.main(
                [
                    "--batch",
                    str(a),
                    str(b),
                    "--output",
                    str(outd),
                    "--workspace",
                    str(self.root),
                ]
            )
        self.assertEqual(code, 0)
        files = list(outd.glob("*.json"))
        self.assertEqual(len(files), 2)

    def test_cli_batch_discover_empty_workspace(self) -> None:
        empty = self.root / "ws"
        empty.mkdir()
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            code = ce.main(["--batch", "--output", str(self.root / "o"), "--workspace", str(empty)])
        self.assertEqual(code, 2)
        self.assertIn("no transcript files", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
