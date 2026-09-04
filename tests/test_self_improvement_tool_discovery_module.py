from __future__ import annotations

import io
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.self_improvement import tool_discovery as td  # noqa: E402


class SelfImprovementToolDiscoveryModuleTests(unittest.TestCase):
    def _mini_repo(self) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        scripts = root / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "__init__.py").write_text('"""scripts"""', encoding="utf-8")
        (scripts / "listed_tool.py").write_text('"""Listed tool."""\n', encoding="utf-8")
        (scripts / "hidden_tool.py").write_text('"""Hidden helper."""\n', encoding="utf-8")
        (root / "TOOLS.md").write_text(
            textwrap.dedent(
                """
                # Tools

                ## Listed Tool

                Already documented.

                Use `python -m scripts.listed_tool`.
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        examples = root / "examples"
        examples.mkdir()
        (examples / "demo_flow.yaml").write_text("description: Demo pipeline\nsteps: []\n", encoding="utf-8")
        return root

    def test_collect_tools_md_registry_finds_module_refs(self) -> None:
        root = self._mini_repo()
        scanner = td._load_workflow_scanner()
        refs, slugs = td.collect_tools_md_registry(root / "TOOLS.md", scanner)
        self.assertIn("scripts.listed_tool", refs)
        self.assertIn("listed-tool", slugs)

    def test_discover_undocumented_excludes_tools_md_entries(self) -> None:
        root = self._mini_repo()
        findings = td.discover_undocumented(root)
        names = {f.name for f in findings}
        self.assertIn("hidden_tool", names)
        self.assertNotIn("listed_tool", names)
        self.assertIn("demo_flow", names)

    def test_main_writes_learnings_report(self) -> None:
        root = self._mini_repo()
        out = root / ".learnings" / "tool_discovery.md"
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            code = td.main(["--root", str(root), "--output", str(out)])
        self.assertEqual(code, 0)
        self.assertTrue(out.is_file())
        text = out.read_text(encoding="utf-8")
        self.assertIn("hidden_tool", text)
        self.assertIn("Why it's valuable", text)
        self.assertIn("python -m src.self_improvement.tool_discovery", text)


if __name__ == "__main__":
    unittest.main()
