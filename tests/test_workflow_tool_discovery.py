from __future__ import annotations

import io
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tool_discovery as td  # noqa: E402


class WorkflowToolDiscoveryTests(unittest.TestCase):
    def test_extract_references_python_m_and_paths(self) -> None:
        text = textwrap.dedent(
            """
            Run `python -m scripts.sync_obsidian` or python3 -m src.ideation.idea_pipeline draft x.
            Legacy: python scripts/obsidian_link_checker.py --vault .
            Shell: bash scripts/healthcheck.sh
            Import style: from scripts.nouz_common import x
            """
        )
        refs = td.extract_references_from_text(text)
        self.assertIn("scripts.sync_obsidian", refs)
        self.assertIn("src.ideation.idea_pipeline", refs)
        self.assertIn("scripts.obsidian_link_checker", refs)
        self.assertIn("scripts/healthcheck.sh", refs)
        self.assertIn("scripts.nouz_common", refs)

    def test_undeployed_lists_only_missing(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        (root / "README.md").write_text(
            "Use python -m scripts.documented_tool\n",
            encoding="utf-8",
        )
        scripts = root / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "__init__.py").write_text('"""pkg"""', encoding="utf-8")
        for name in ("documented_tool", "orphan_tool"):
            (scripts / f"{name}.py").write_text(
                '"""Tool."""\nprint("x")\n',
                encoding="utf-8",
            )

        report = td.build_workflow_gap_report(root)
        ids = {c.tool_id for c in report.undeployed}
        self.assertIn("scripts.orphan_tool", ids)
        self.assertNotIn("scripts.documented_tool", ids)

    def test_json_report_shape(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        (root / "README.md").write_text("# empty\n", encoding="utf-8")
        scripts = root / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "solo.py").write_text('"""solo."""\n', encoding="utf-8")

        report = td.build_workflow_gap_report(root)
        payload = json.loads(td.render_workflow_gap_json(report))
        self.assertEqual(payload["repo_root"], str(root.resolve()))
        self.assertIn("undeployed", payload)
        self.assertTrue(any(x["tool_id"] == "scripts.solo" for x in payload["undeployed"]))

    def test_scripts_tool_discovery_workflow_subcommand(self) -> None:
        from scripts import tool_discovery as std

        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        (root / "README.md").write_text("# x\n", encoding="utf-8")
        scripts = root / "scripts"
        scripts.mkdir()
        (scripts / "z_cli.py").write_text('"""z."""\n', encoding="utf-8")

        buf = io.StringIO()
        err = io.StringIO()
        prev_out, prev_err = sys.stdout, sys.stderr
        try:
            sys.stdout = buf
            sys.stderr = err
            code = std.main(["--root", str(root), "workflow", "--report-format", "json"])
        finally:
            sys.stdout = prev_out
            sys.stderr = prev_err

        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertTrue(any(u["tool_id"] == "scripts.z_cli" for u in data["undeployed"]))


if __name__ == "__main__":
    unittest.main()
