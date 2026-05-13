from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import tool_discovery  # noqa: E402


class OpenClawToolDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.repo = Path(self._td.name)
        self.env_old = os.environ.get("TOOL_DISCOVERY_ROOT")
        os.environ["TOOL_DISCOVERY_ROOT"] = str(self.repo)

    def tearDown(self) -> None:
        if self.env_old is None:
            os.environ.pop("TOOL_DISCOVERY_ROOT", None)
        else:
            os.environ["TOOL_DISCOVERY_ROOT"] = self.env_old

    def _write_openclaw_tool_js(self) -> None:
        dist = self.repo / "node_modules" / "openclaw" / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "tools.js").write_text(
            textwrap.dedent(
                """
                export const x = {
                  type: "function",
                  function: {
                    name: "alpha_read",
                    description: "Read alpha channel",
                    parameters: { "type": "object", "properties": { "path": { "type": "string" } } }
                  }
                };
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

    def _write_skill_md(self) -> None:
        skills = self.repo / "skills" / "demo"
        skills.mkdir(parents=True, exist_ok=True)
        (skills / "SKILL.md").write_text(
            textwrap.dedent(
                """
                ---
                name: Demo Skill
                description: Demonstrates markdown skill discovery.
                ---
                Body ignored for description.
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

    def _write_skill_python_tool(self) -> None:
        py_dir = self.repo / "src" / "skills"
        py_dir.mkdir(parents=True, exist_ok=True)
        (py_dir / "boxed.py").write_text(
            textwrap.dedent(
                '''
                TOOL = {
                    "name": "boxed_ping",
                    "description": "Ping helper",
                    "parameters": {"type": "object", "properties": {}},
                }
                '''
            ).strip()
            + "\n",
            encoding="utf-8",
        )

    def test_scan_collects_dist_js_skill_md_and_python(self) -> None:
        self._write_openclaw_tool_js()
        self._write_skill_md()
        self._write_skill_python_tool()

        tools = tool_discovery.scan_all(self.repo)
        names = {t.name for t in tools}
        self.assertIn("alpha_read", names)
        self.assertIn("Demo Skill", names)
        self.assertIn("boxed_ping", names)

    def test_scan_command_writes_json_tools_md_and_last_scan(self) -> None:
        self._write_openclaw_tool_js()
        self._write_skill_md()

        exit_code = tool_discovery.main(["scan"])
        self.assertEqual(exit_code, 0)

        learn = self.repo / ".learnings" / "tools"
        self.assertTrue((self.repo / "tools.md").exists())
        json_files = [p for p in learn.glob("*.json") if p.name != "_last_scan.json"]
        self.assertGreaterEqual(len(json_files), 2)
        self.assertTrue((learn / "_last_scan.json").exists())

    def test_scan_detects_new_tool_on_second_run(self) -> None:
        self._write_openclaw_tool_js()
        self.assertEqual(tool_discovery.main(["scan"]), 0)

        dist = self.repo / "node_modules" / "openclaw" / "dist"
        (dist / "extra.js").write_text(
            textwrap.dedent(
                """
                const t = {
                  type: "function",
                  function: {
                    name: "brand_new_tool",
                    description: "Appears on second scan",
                    parameters: {}
                  }
                };
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        buf_err: list[str] = []

        class _Err:
            def write(self, s: str) -> int:
                buf_err.append(s)
                return len(s)

            def flush(self) -> None:
                return None

        prev_err = sys.stderr
        try:
            sys.stderr = _Err()
            exit_code = tool_discovery.main(["scan"])
        finally:
            sys.stderr = prev_err

        self.assertEqual(exit_code, 0)
        joined = "".join(buf_err)
        self.assertIn("NOTICE", joined)
        self.assertIn("brand_new_tool", joined)

    def test_list_and_show(self) -> None:
        self._write_openclaw_tool_js()
        self.assertEqual(tool_discovery.main(["scan"]), 0)

        buf = io.StringIO()
        prev = sys.stdout
        try:
            sys.stdout = buf
            self.assertEqual(tool_discovery.main(["list", "--filter", "alpha"]), 0)
        finally:
            sys.stdout = prev
        out = buf.getvalue()
        self.assertIn("alpha_read", out)

        show_buf = io.StringIO()
        try:
            sys.stdout = show_buf
            self.assertEqual(tool_discovery.main(["show", "alpha_read"]), 0)
        finally:
            sys.stdout = prev
        record = json.loads(show_buf.getvalue())
        self.assertEqual(record["name"], "alpha_read")
        self.assertEqual(record["source_kind"], "openclaw_dist")


if __name__ == "__main__":
    unittest.main()
