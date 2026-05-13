from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.self_improvement import tool_discovery as td  # noqa: E402


class SelfImprovementToolDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.oc = self.root / ".openclaw"
        (self.oc / "skills" / "alpha").mkdir(parents=True)
        (self.oc / "skills" / "alpha" / "SKILL.md").write_text(
            "---\nname: Alpha Skill\n---\nBody\n",
            encoding="utf-8",
        )
        (self.oc / "tools" / "mcp_widget").mkdir(parents=True)
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "scripts" / "__init__.py").write_text('"""scripts"""', encoding="utf-8")
        (self.root / "scripts" / "zeta_helper.py").write_text(
            '"""Does zeta things."""\nprint("zeta")\n',
            encoding="utf-8",
        )
        self.mem = self.root / "memory"
        self.mem.mkdir(parents=True)
        session = {
            "messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "name": "read_file", "input": {}}]},
                {"role": "assistant", "content": "invoking skill Alpha Skill next"},
            ]
        }
        (self.mem / "sess.json").write_text(json.dumps(session), encoding="utf-8")

    def test_catalog_and_report(self) -> None:
        out = self.root / ".learnings" / "tool_discovery.md"
        argv = [
            "--repo-root",
            str(self.root),
            "--openclaw-home",
            str(self.oc),
            "--since-days",
            "1",
            "--max-files",
            "50",
            "--output",
            str(out),
        ]
        with patch.object(sys, "stdout", new=io.StringIO()):
            self.assertEqual(td.main(argv), 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("Alpha Skill", text)
        self.assertIn("mcp_widget", text)
        self.assertIn("zeta_helper", text)
        self.assertIn("read_file", text)
        self.assertIn("Catalog vs session hits", text)


if __name__ == "__main__":
    unittest.main()
